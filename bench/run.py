"""Run benchmark tasks through the real agent8088 CLI, then grade each run independently.

    python bench/run.py --profile <profile.json> [--task <id> ...] [--trials N] [--out <folder>]

Each run gets its own folder with a fresh workspace (a copy of the task's
fixture), its own Agent8088 home and config, and its own trace folder, so a run
never reads or writes the developer's own ~/.agent8088. The agent runs once with
`--prompt-file`; then the task's grader checks the workspace on its own, without
the agent's help. One result.json per run, and one line per run in
<out>/results.jsonl, feed bench/report.py.

The profile is a JSON file kept outside the repository:

    {
      "name": "hyperqwen",
      "config": {"default_provider": "hyperqwen", "provider.hyperqwen.base_url": "...", ...},
      "env_file": "path/to/a/.env with the provider key",
      "env_keys": ["HYPERQWEN_API_KEY"],
      "shared_from": "the Agent8088 data folder whose sandbox runtime to reuse",
      "price_in": 0.0, "price_out": 0.0
    }

Only the keys named in env_keys are copied from env_file into the run's own
.env. Their values are never printed or written to a result.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import benchlib

# Same budget rules as the Terminal-Bench adapter: the loop stops itself a little
# before the watchdog, and no one shell call can use up the run's time.
SELF_STOP_SEC = 15
WATCHDOG_GRACE_SEC = 60
GRADER_TIMEOUT_SEC = 900
SERVICE_START_SEC = 30
_END_RE = re.compile(r"\[agent8088\] run ended: ([a-z_]+)")


def budget_config(budget_sec: int) -> dict:
    return {
        "max_turn_seconds": str(max(60, budget_sec - SELF_STOP_SEC)),
        "max_tool_timeout_seconds": str(max(60, budget_sec - 120)),
        "tool_timeout.execute_shell": "600" if budget_sec >= 1500 else "300",
    }


def run_config(profile: dict, task: dict, workspace: Path) -> dict:
    """The run's config.txt: the profile, the task's needs, then what the benchmark fixes."""
    config = dict(profile.get("config") or {})
    config.update(task.get("config") or {})
    config.update(budget_config(int(task["budget_sec"])))
    config.update({
        "project_root": str(workspace),
        "shell_cwd": str(workspace),
        "show_trace": "1",
        # Streamed replies carry token usage only when model telemetry asks the
        # server for it; without it input tokens read 0 and output is estimated.
        "model_telemetry": "1",
    })
    return config


def write_config(path: Path, config: dict) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in config.items()), encoding="utf-8")


def copy_env(profile: dict, dest: Path) -> list:
    """Copy only the named keys into the run's .env; return the names that were found."""
    source = profile.get("env_file")
    wanted = set(profile.get("env_keys") or [])
    found, lines = [], []
    if source and wanted:
        for line in Path(source).expanduser().read_text(encoding="utf-8").splitlines():
            name = line.split("=", 1)[0].strip().removeprefix("export ").strip()
            if name in wanted and "=" in line:
                lines.append(line.strip().removeprefix("export ").strip())
                found.append(name)
    dest.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return sorted(set(found))


# Folders of an Agent8088 data folder a run may reuse: the sandbox runtime, the
# bundled Node and the browser download. They hold generic binaries, never
# settings or keys. The small runtime is copied so a run cannot change the
# original; the large ones are linked.
COPIED = ("runtime",)
LINKED = ("node", "playwright-browsers")
# The WSL sandbox's setup record (Windows): which distro the shared install set up, so a run uses it too.
COPIED_FILES = ("wsl-sandbox.json",)


def _link_dir(source: Path, dest: Path) -> None:
    if os.name == "nt":
        # A junction needs no special rights on Windows, unlike a symlink.
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(dest), str(source)],
                              capture_output=True, check=False)
        if done.returncode == 0:
            return
    else:
        try:
            dest.symlink_to(source, target_is_directory=True)
            return
        except OSError:
            pass
    shutil.copytree(source, dest)


def share_runtime(profile: dict, home: Path) -> list:
    """Make the profile's shared_from folders available in the run's home; return their names.

    Without them a fresh home has no native sandbox, and Agent8088 falls back to
    Docker, which has no network, so the run would not measure the usual setup."""
    source = profile.get("shared_from")
    if not source:
        return []
    source = Path(source).expanduser()
    shared = []
    for name in COPIED + LINKED:
        if not (source / name).is_dir() or (home / name).exists():
            continue
        if name in COPIED:
            shutil.copytree(source / name, home / name)
        else:
            _link_dir(source / name, home / name)
        shared.append(name)
    for name in COPIED_FILES:
        if (source / name).is_file() and not (home / name).exists():
            shutil.copy2(source / name, home / name)
            shared.append(name)
    return shared


def _stop(process: subprocess.Popen) -> None:
    """Stop the agent and everything it started."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class ServiceError(RuntimeError):
    """A task's service could not be started, so the task cannot run."""


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def service_command(command: list, task: dict, state: Path) -> list:
    """A service's command with {python}, {task} and {state} filled in."""
    values = {"{python}": sys.executable, "{task}": str(task["dir"]), "{state}": str(state)}
    filled = []
    for part in command:
        for key, value in values.items():
            part = part.replace(key, value)
        filled.append(part)
    return filled


def start_services(task: dict, run_dir: Path, wait_sec: float = SERVICE_START_SEC) -> list:
    """Start the task's services (mock web sites, a mock app) and wait until every port answers.

    They run from the task folder, so the agent's workspace never holds their source. Their state lives in
    <run>/state, which the grader also reads. A port that is already in use stops the run: answering with some
    other program would make the grade meaningless."""
    services = task.get("services") or []
    if not services:
        return []
    state = run_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    busy = [port for service in services for port in service["ports"] if _port_open(port)]
    if busy:
        raise ServiceError(f"ports already in use before the run: {', '.join(map(str, busy))}")
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    processes = []
    try:
        with open(run_dir / "services.log", "a", encoding="utf-8", errors="replace") as log:
            for service in services:
                process = subprocess.Popen(service_command(service["command"], task, state), cwd=task["dir"],
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kwargs)
                processes.append(process)
                deadline = time.monotonic() + wait_sec
                while not all(_port_open(port) for port in service["ports"]):
                    if process.poll() is not None:
                        raise ServiceError(f"service {service['command']} exited with {process.returncode}")
                    if time.monotonic() > deadline:
                        raise ServiceError(f"service {service['command']} did not open {service['ports']}")
                    time.sleep(0.2)
    except BaseException:
        stop_services(processes)
        raise
    return processes


def stop_services(processes: list) -> None:
    for process in processes:
        _stop(process)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass


def run_agent(agent: str, prompt: Path, workspace: Path, env: dict, log: Path, limit_sec: int) -> dict:
    started, clock_start = benchlib.now_iso(), time.monotonic()
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with open(log, "w", encoding="utf-8", errors="replace") as out:
        process = subprocess.Popen([agent, "--prompt-file", str(prompt)], cwd=workspace, env=env,
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kwargs)
        stopped_by = "none"
        try:
            code = process.wait(timeout=limit_sec)
        except subprocess.TimeoutExpired:
            stopped_by = "watchdog"
            _stop(process)
            code = process.wait()
    ended = log.read_text(encoding="utf-8", errors="replace")
    reasons = _END_RE.findall(ended)
    return {
        "started": started,
        "ended": benchlib.now_iso(),
        "elapsed_sec": round(time.monotonic() - clock_start, 1),
        "exit_code": code,
        "stopped_by": stopped_by,
        "end_reason": "watchdog" if stopped_by == "watchdog" else (reasons[-1] if reasons else "unknown"),
    }


def latest_trace(folder: Path):
    traces = sorted(folder.glob("agent8088-trace-*.json"))
    if not traces:
        return None, None
    try:
        return traces[-1], json.loads(traces[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return traces[-1], None


def grader_env(profile: dict) -> dict:
    """The grader's environment: this one, plus the shared browser download when there is one."""
    env = dict(os.environ)
    browsers = Path(profile.get("shared_from") or "").expanduser() / "playwright-browsers"
    if profile.get("shared_from") and browsers.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in env:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    return env


def grade(task: dict, workspace: Path, run_dir: Path, profile: dict | None = None) -> dict:
    """Run the task's grader on the finished workspace; it writes a JSON report."""
    report = run_dir / "grade.json"
    command = [sys.executable, str(task["dir"] / task["grader"]), str(workspace), "--json", str(report)]
    if task.get("services"):
        command += ["--state", str(run_dir / "state")]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=GRADER_TIMEOUT_SEC, check=False,
                              cwd=run_dir, encoding="utf-8", errors="replace", env=grader_env(profile or {}))
    except subprocess.TimeoutExpired:
        return {"error": f"grader timed out after {GRADER_TIMEOUT_SEC}s"}
    (run_dir / "grade.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"error": f"grader wrote no report (exit {done.returncode})"}
    if "passed" not in data:
        return {"error": "grader report has no 'passed' field"}
    return data


def _runner_venv():
    return Path(sys.prefix) if sys.prefix != sys.base_prefix else None


def agent_env(profile: dict, runner_venv=None) -> dict:
    """The environment the agent starts with: this one, minus what belongs to the benchmark.

    Left out: any AGENT8088_* setting (the run sets its own), the profile's keys
    (the run reads them from its own .env), and the benchmark's own virtual
    environment, which `uv run` puts first on PATH. With it there, the agent's
    `python -m venv` built on the benchmark's Python, not the machine's."""
    venv = Path(runner_venv) if runner_venv else _runner_venv()
    keys = set(profile.get("env_keys") or [])
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("AGENT8088_") and key not in keys and key != "VIRTUAL_ENV"}
    if venv:
        inside = str(venv.resolve()).lower()
        path_key = next((key for key in env if key.upper() == "PATH"), "PATH")
        def in_venv(entry):
            where = str(Path(entry).resolve()).lower()
            return where == inside or where.startswith(inside + os.sep)
        kept = [entry for entry in env.get(path_key, "").split(os.pathsep) if entry and not in_venv(entry)]
        env[path_key] = os.pathsep.join(kept)
    return env


def run_one(task: dict, profile: dict, run_dir: Path, agent: str) -> dict:
    workspace, home, traces = run_dir / "workspace", run_dir / "home", run_dir / "trace"
    for folder in (workspace, home, traces):
        folder.mkdir(parents=True, exist_ok=True)
    if task["fixture"]:
        shutil.copytree(task["dir"] / task["fixture"], workspace, dirs_exist_ok=True)
    prompt = run_dir / "prompt.md"
    shutil.copyfile(task["dir"] / task["prompt"], prompt)
    write_config(home / "config.txt", run_config(profile, task, workspace))
    keys = copy_env(profile, home / ".env")
    shared = share_runtime(profile, home)
    env = agent_env(profile)
    env.update({
        "AGENT8088_HOME": str(home),
        "AGENT8088_CONFIG": str(home / "config.txt"),
        "AGENT8088_TRACE_DIR": str(traces),
        "AGENT8088_UNATTENDED": "1",
        "AGENT8088_PERMISSION": "full-auto",
        "PYTHONIOENCODING": "utf-8",
    })
    budget = int(task["budget_sec"])
    services = start_services(task, run_dir)
    try:
        return _run_and_grade(task, profile, run_dir, agent, env, keys, shared, budget, services)
    finally:
        stop_services(services)


def _run_and_grade(task, profile, run_dir, agent, env, keys, shared, budget, services) -> dict:
    workspace, traces, prompt = run_dir / "workspace", run_dir / "trace", run_dir / "prompt.md"
    facts = run_agent(agent, prompt, workspace, env, run_dir / "agent.log", budget + WATCHDOG_GRACE_SEC)
    trace_path, trace = latest_trace(traces)
    summary = benchlib.summarize_trace(trace, end_reason=facts["end_reason"])
    result = {
        "task": task["id"], "kind": task["kind"], "model": profile.get("name", "unnamed"),
        "served_model": (trace or {}).get("model"), "budget_sec": budget, "env_keys_found": keys,
        "shared_runtime": shared,
        "sandbox_backend": benchlib.sandbox_backend(
            (run_dir / "agent.log").read_text(encoding="utf-8", errors="replace")),
        "trace_file": trace_path.name if trace_path else None, "platform": sys.platform,
        **facts, **summary,
    }
    result["cost_usd"] = benchlib.cost_usd(summary["input_tokens"], summary["output_tokens"], profile)
    result["grader"] = grade(task, workspace, run_dir, profile)
    result["outcome"] = benchlib.outcome(result)
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def resolve_agent(agent: str) -> str:
    """The agent executable as a full path. Each run starts it from inside the task's workspace, so a relative path or
    a bare name that is not on PATH would not be found there; the first baseline launch failed that way."""
    candidate = Path(agent).expanduser()
    if candidate.parent != Path(".") or candidate.exists():
        resolved = candidate.resolve()
        if resolved.is_file():
            return str(resolved)
        for suffix in (".exe", ".cmd", ".bat"):
            if resolved.with_name(resolved.name + suffix).is_file():
                return str(resolved.with_name(resolved.name + suffix))
        raise SystemExit(f"agent executable not found: {agent}")
    found = shutil.which(agent)
    if not found:
        raise SystemExit(f"agent executable not found on PATH: {agent}")
    return str(Path(found).resolve())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", help="model profile JSON, kept outside the repository (needed to run)")
    parser.add_argument("--task", action="append", default=[], help="task id (repeatable); default all")
    parser.add_argument("--kind", choices=benchlib.TASK_KINDS, help="only tasks of this kind")
    parser.add_argument("--trials", type=int, help="runs per task (default 1, or the tier's own)")
    parser.add_argument("--tier", help="run a tier from bench/tiers.json: its tasks and trials; a run that says "
                                       "nothing about the agent (infra) is repeated once")
    parser.add_argument("--out", default="bench-runs", help="folder for run folders and results.jsonl")
    parser.add_argument("--agent", default="agent8088", help="the agent8088 executable to run")
    parser.add_argument("--list", action="store_true", help="list the tasks and exit")
    args = parser.parse_args(argv)

    tasks = benchlib.discover_tasks(HERE / "tasks")
    if args.list:
        for task in tasks:
            print(f"{task['id']:<28} {task['kind']:<8} {task['budget_sec']:>6}s  {task.get('title', '')}")
        return 0
    if not args.profile:
        parser.error("the following arguments are required: --profile")
    repeat_infra = False
    tier = None
    if args.tier:
        tier = benchlib.load_tier(args.tier)
        args.task = args.task or list(tier["tasks"])
        repeat_infra = True
    if args.task:
        unknown = set(args.task) - {task["id"] for task in tasks}
        if unknown:
            parser.error(f"unknown task: {', '.join(sorted(unknown))}")
        tasks = [task for task in tasks if task["id"] in args.task]
    if args.kind:
        tasks = [task for task in tasks if task["kind"] == args.kind]
    agent = resolve_agent(args.agent)
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        trials = benchlib.tier_trials(tier, task["id"], args.trials) if tier else (args.trials or 1)
        for trial in range(1, trials + 1):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            run_dir = out / f"{stamp}_{task['id']}_{profile.get('name', 'unnamed')}_{trial}"
            print(f"{benchlib.clock(benchlib.now_iso())} start {run_dir.name}", flush=True)
            try:
                result = run_one(task, profile, run_dir, agent)
                if repeat_infra and benchlib.is_infra(result):
                    # The grader could not run, or no model reply came back: the run says nothing about the agent.
                    # Keep it in its folder and try once more.
                    print(f"{benchlib.clock(result['ended'])} infra {run_dir.name}: "
                          f"{benchlib.likely_cause(result)[1]}; repeating once", flush=True)
                    run_dir = run_dir.with_name(run_dir.name + "_repeat")
                    result = run_one(task, profile, run_dir, agent)
            except ServiceError as error:
                print(f"{benchlib.clock(benchlib.now_iso())} skip  {run_dir.name}: {error}", flush=True)
                continue
            with open(out / "results.jsonl", "a", encoding="utf-8") as lines:
                lines.write(json.dumps(result) + "\n")
            grader = result["grader"]
            verdict = "passed" if grader.get("passed") else ("ungraded" if "error" in grader else "failed")
            print(f"{benchlib.clock(result['ended'])} end   {run_dir.name}: {result['elapsed_sec']:.0f}s, "
                  f"harness {result['harness_verdict']}, grader {verdict}, {result['outcome']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
