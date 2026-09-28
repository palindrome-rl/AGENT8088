"""The container-side pieces of the Harbor adapter, run with real bash.

agent.py itself imports Harbor and is exercised by a live `harbor run`; these
cover the parts that do not need Harbor: the config rewrite and the launcher.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

# These scripts run inside Linux benchmark containers. Git Bash on Windows
# cannot use Python's Windows temp paths as bash paths.
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux container scripts")

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "test-mode-setting"
sys.path.insert(0, str(BENCH))

import json  # noqa: E402

from agent8088_harbor.config_edit import (  # noqa: E402
    SHELL_TIMEOUT_SENTENCE, budget_edits, config_edit_command, tools_description_command,
    trace_token_totals)


def test_budget_edits_size_timeouts_to_the_task():
    short = budget_edits(880)    # a 900 s task
    assert short == {"max_turn_seconds": "865", "max_tool_timeout_seconds": "760",
                     "tool_timeout.execute_shell": "300"}

    long = budget_edits(1780)    # an 1800 s task
    assert long["tool_timeout.execute_shell"] == "600"
    # Sampling is fixed in config.txt, never varied per task.
    assert not {"temperature", "provider.vllm.extra_body"} & set(long)

    assert budget_edits(0) == {}


def test_trace_token_totals_sum_the_turns():
    data = {"trace": [{"usage": {"input_tokens": 100, "output_tokens": 20}},
                      {"usage": None},
                      {"usage": {"input_tokens": 50, "output_tokens": 5}}]}
    assert trace_token_totals(data) == (150, 25)
    assert trace_token_totals({"trace": [{"steps": []}]}) is None   # older agent8088
    assert trace_token_totals({"messages": [], "trace": []}) is None


def test_config_edit_replaces_keys_and_keeps_the_rest(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text("# comment\nprovider.vllm.base_url=http://old/v1\nmemory=0\n"
                   "provider.vllmX.base_url=keep\nmodel_name=old\n", encoding="utf-8")
    edits = {"provider.vllm.base_url": "http://gpu:8000/v1", "model_name": "qwen3.8-27b",
             "max_turn_seconds": "840"}
    subprocess.run(["bash", "-c", config_edit_command(str(cfg), edits)], check=True)
    lines = cfg.read_text(encoding="utf-8").splitlines()
    assert "# comment" in lines and "memory=0" in lines
    assert "provider.vllmX.base_url=keep" in lines   # the dot is literal, not a wildcard
    assert lines.count("provider.vllm.base_url=http://gpu:8000/v1") == 1
    assert "provider.vllm.base_url=http://old/v1" not in lines
    assert lines.count("model_name=qwen3.8-27b") == 1
    assert "max_turn_seconds=840" in lines


def test_config_edit_quotes_hostile_values(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text("model_name=x\n", encoding="utf-8")
    subprocess.run(["bash", "-c", config_edit_command(str(cfg), {"model_name": "a'; touch pwned; '"})],
                   check=True, cwd=tmp_path)
    assert not (tmp_path / "pwned").exists()
    assert "model_name=a'; touch pwned; '" in cfg.read_text(encoding="utf-8")


def _launcher(tmp_path, fake_body, budget):
    """Run run-agent.sh with /opt/agent8088/bin/agent8088 replaced by a fake."""
    script = (BENCH / "container" / "run-agent.sh").read_text(encoding="utf-8")
    fake = tmp_path / "agent8088"
    fake.write_text("#!/bin/bash\n" + fake_body, encoding="utf-8")
    fake.chmod(0o755)
    logs = tmp_path / "logs"
    script = script.replace("/opt/agent8088/bin/agent8088", str(fake)).replace("/logs/agent", str(logs))
    launcher = tmp_path / "run-agent.sh"
    launcher.write_text(script, encoding="utf-8")
    instruction = tmp_path / "instruction.md"
    instruction.write_text("do it", encoding="utf-8")
    env = {**os.environ, "AGENT8088_HOME": str(tmp_path / "home"), "AGENT8088_WORKDIR": str(tmp_path)}
    result = subprocess.run(["bash", str(launcher), str(instruction), str(budget)],
                            capture_output=True, text=True, env=env, timeout=60)
    return result, logs / "agent8088.txt"


def test_launcher_passes_the_instruction_file_and_logs(tmp_path):
    result, log = _launcher(tmp_path, 'echo "got $1 $(cat $2)"; pwd\n', budget=0)
    assert result.returncode == 0, result.stderr
    assert f"got --prompt-file do it" in log.read_text()
    assert str(tmp_path) in log.read_text()   # ran in AGENT8088_WORKDIR


def test_launcher_budget_stops_the_agent_and_exits_zero(tmp_path):
    if subprocess.run(["bash", "-c", "command -v timeout"], capture_output=True).returncode:
        import pytest
        pytest.skip("coreutils timeout not installed on this host")
    result, _ = _launcher(tmp_path, "sleep 30\n", budget=2)
    assert result.returncode == 0
    assert "stopped at the 2s budget (watchdog)" in result.stdout
    record = json.loads((tmp_path / "logs" / "agent8088-exit.json").read_text())
    assert record["stopped_by"] == "watchdog" and record["exit_code"] == 124
    assert record["end_reason"] == "watchdog"
    assert record["budget_sec"] == 2 and record["elapsed_sec"] >= 2


def _exit_record(tmp_path):
    return json.loads((tmp_path / "logs" / "agent8088-exit.json").read_text())


def test_launcher_records_a_normal_finish(tmp_path):
    result, _ = _launcher(tmp_path, 'echo "[agent8088] run ended: answered"\n', budget=30)
    assert result.returncode == 0 and "stopped at the" not in result.stdout
    record = _exit_record(tmp_path)
    assert record["exit_code"] == 0 and record["stopped_by"] == "none"
    assert record["end_reason"] == "answered"


def test_launcher_reports_the_agents_own_time_stop_as_a_budget_stop(tmp_path):
    result, _ = _launcher(tmp_path, 'echo "[agent8088] run ended: time_budget"\n', budget=30)
    assert result.returncode == 0
    assert "stopped at the 30s budget (self)" in result.stdout
    assert _exit_record(tmp_path)["stopped_by"] == "self"


def test_launcher_records_a_model_error_without_calling_it_a_timeout(tmp_path):
    result, _ = _launcher(tmp_path, 'echo "[agent8088] run ended: model_error"\n', budget=30)
    assert "stopped at the" not in result.stdout
    record = _exit_record(tmp_path)
    assert record["end_reason"] == "model_error" and record["stopped_by"] == "none"


def test_launcher_propagates_real_failures(tmp_path):
    result, _ = _launcher(tmp_path, "exit 3\n", budget=0)
    assert result.returncode == 3


def test_run_script_invokes_harbor_per_task_with_its_budget(tmp_path):
    tb = tmp_path / "tb" / "tasks"
    for name, timeout in (("fix-git", 900), ("mailman", 1800)):
        (tb / name).mkdir(parents=True)
        (tb / name / "task.toml").write_text(f"[agent]\ntimeout_sec = {timeout}.0\n", encoding="utf-8")
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("# comment\nfix-git\n\nmailman\n", encoding="utf-8")
    wheel = tmp_path / "agent8088-0-py3-none-any.whl"
    wheel.write_text("", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    (bindir / "harbor").write_text(f'#!/bin/bash\necho "$@" >> {calls}\n', encoding="utf-8")
    (bindir / "harbor").chmod(0o755)
    # The docker-access guard must not depend on the test host: fake a working
    # daemon so run.sh takes neither the sg re-exec nor the exit-3 path.
    (bindir / "docker").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (bindir / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    subprocess.run(["bash", str(BENCH / "run.sh"), "--tb", str(tmp_path / "tb"), "--wheel", str(wheel),
                    "--tasks", str(tasks), "--base-url", "http://gpu:8000/v1"],
                   check=True, env=env, capture_output=True, text=True, timeout=60)
    lines = calls.read_text().splitlines()
    assert len(lines) == 2
    assert "-i fix-git" in lines[0] and "budget_sec=880" in lines[0]
    assert "-i mailman" in lines[1] and "budget_sec=1780" in lines[1]
    # Every Harbor timeout multiplier stays at its default 1.0 (Terminus-2/Pi parity).
    assert not any("multiplier" in line for line in lines)
    assert all("-n 1" in line and "base-url" not in line and "http://gpu:8000/v1" in line for line in lines)
    assert all(f"wheel_path={wheel}" in line for line in lines)


def test_run_script_extra_compose_replaces_the_default(tmp_path):
    tb = tmp_path / "tb" / "tasks"
    tb.mkdir(parents=True)
    (tb / "fix-git").mkdir()
    (tb / "fix-git" / "task.toml").write_text("[agent]\ntimeout_sec = 900.0\n", encoding="utf-8")
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("fix-git\n", encoding="utf-8")
    wheel = tmp_path / "agent8088-0-py3-none-any.whl"
    wheel.write_text("", encoding="utf-8")
    custom = tmp_path / "host-network.yaml"
    custom.write_text("services:\n  main:\n    network_mode: host\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    (bindir / "harbor").write_text(f'#!/bin/bash\necho "$@" >> {calls}\n', encoding="utf-8")
    (bindir / "harbor").chmod(0o755)
    (bindir / "docker").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (bindir / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    subprocess.run(["bash", str(BENCH / "run.sh"), "--tb", str(tmp_path / "tb"), "--wheel", str(wheel),
                    "--tasks", str(tasks), "--extra-compose", str(custom)],
                   check=True, env=env, capture_output=True, text=True, timeout=60)
    line = calls.read_text().splitlines()[0]
    assert f"--extra-docker-compose {custom}" in line
    assert "host-gateway.compose.yaml" not in line


def test_tasks_file_lists_all_89_tasks():
    """The full Terminal-Bench 2.1 run set: 89 unique task names."""
    names = [ln.strip() for ln in (BENCH / "tasks.txt").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    assert len(names) == 89, len(names)
    assert len(set(names)) == 89


def test_run_script_reports_harbor_failures_after_running_every_task(tmp_path):
    tb = tmp_path / "tb" / "tasks"
    for name in ("fix-git", "mailman"):
        (tb / name).mkdir(parents=True)
        (tb / name / "task.toml").write_text("[agent]\ntimeout_sec = 900.0\n", encoding="utf-8")
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("fix-git\nmailman\n", encoding="utf-8")
    wheel = tmp_path / "agent8088-0-py3-none-any.whl"
    wheel.write_text("", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    (bindir / "harbor").write_text(f'#!/bin/bash\necho "$@" >> {calls}\nexit 7\n', encoding="utf-8")
    (bindir / "harbor").chmod(0o755)
    (bindir / "docker").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (bindir / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(BENCH / "run.sh"), "--tb", str(tmp_path / "tb"),
                             "--wheel", str(wheel), "--tasks", str(tasks)],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    assert len(calls.read_text().splitlines()) == 2        # both tasks still ran
    assert "failed for 2 task(s): fix-git mailman" in result.stderr


def test_launcher_copies_agent8088s_internal_log_even_after_a_watchdog_stop(tmp_path):
    if subprocess.run(["bash", "-c", "command -v timeout"], capture_output=True).returncode:
        pytest.skip("coreutils timeout not installed on this host")
    body = ('mkdir -p "$AGENT8088_HOME/logs"; echo \'{"msg": "auto-compacted"}\' '
            '> "$AGENT8088_HOME/logs/agent8088-2026-09-26.log"; sleep 30\n')
    result, _ = _launcher(tmp_path, body, budget=2)
    assert "stopped at the 2s budget (watchdog)" in result.stdout
    copied = tmp_path / "logs" / "agent8088-internal-logs" / "agent8088-2026-09-26.log"
    assert "auto-compacted" in copied.read_text()


def test_tools_txt_states_the_tasks_real_shell_timeouts(tmp_path):
    shipped = (BENCH / "tools.txt").read_text(encoding="utf-8")
    # The adapter rewrites this exact sentence; if it drifts, the rewrite would no-op.
    assert SHELL_TIMEOUT_SENTENCE in shipped
    tools = tmp_path / "tools.txt"
    tools.write_text(shipped, encoding="utf-8")
    subprocess.run(["bash", "-c", tools_description_command(str(tools), 880)], check=True)
    text = tools.read_text(encoding="utf-8")
    assert "up to 760) for installs, builds and long runs; the default is 300." in text
    assert SHELL_TIMEOUT_SENTENCE not in text
    others = lambda t: [l for l in t.splitlines() if not l.startswith("execute_shell|")]  # noqa: E731
    assert others(text) == others(shipped)
    assert tools_description_command(str(tools), 0) == "true"      # no budget: unchanged
