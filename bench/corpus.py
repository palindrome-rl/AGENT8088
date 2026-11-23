"""Tier 0: saved benchmark runs, replayed through the current verdict code.

A live benchmark run takes minutes to hours and a GPU. Most changes to the verdict code do not need one: what decides
"done" or "NOT VERIFIED" is the model's reply, the commands it ran and what they printed, the files it left, and the
output of the page/script check. A case keeps those, scrubbed of local paths, and `replay` judges them again with the
code of the branch under test. `tests/test_verdict_corpus.py` replays every case on every PR.

    python bench/corpus.py export <run folder> --id <case id>      # add a saved run as a case
    python bench/corpus.py replay [<case id> ...]                   # print what the current code says

What is replayed with the current code:
- the reply's own words (a part not done, a check that failed)
- every shell command and its output, in order, through the trajectory tracker (failing check, environment limits)
- the page/script check of every script, from the output it gave when it was started
- the secret scan of the files the run wrote
- the parts of the request, against the reply and the files

What is kept as it was recorded, because it cannot be redone without the run: the page check of a page in a
browser, a file cut off mid-write, and the unmet checks the run noted. Writes are taken as done before the first
command, and no command counts as changing files, so a case is a close copy of the run, not an exact one.

Each case has a label in `tests/verdict_corpus/labels.json`:
- `expected`: "done" or "not_verified", the verdict a correct harness gives on this evidence. It is not always the
  grader's: work the grader passes is still rightly NOT VERIFIED when the run's own check failed and was never fixed.
- `blame`: who a wrong result belongs to.
  - "none": the work was right and the verdict too
  - "model": the work was wrong, or left unchecked, and the harness said so
  - "harness": the verdict is wrong on this evidence
  - "environment": the sandbox or the machine stopped the check, and the harness said so
  - "grader": the grader was wrong (the case is still labelled with the right verdict)
- `known_gap`: the current code is known to get this case wrong. The test expects it to fail; when a fix makes it
  pass, the label has to be updated in the same change.
- `note`: one line on what the case shows.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CORPUS = ROOT / "tests" / "verdict_corpus"
WS = "<WS>"

# Folders inside a workspace that are not the run's work.
_SKIP_PARTS = {"node_modules", ".venv", "venv", "__pycache__", ".git", "home", "playwright-browsers"}
_MAX_FILE = 120_000
_MAX_FILES_TOTAL = 900_000
_MAX_RESULT = 12_000
_TEXT_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".html", ".htm", ".css", ".json", ".md", ".txt", ".csv",
                  ".toml", ".yaml", ".yml", ".ini", ".cfg", ".sh", ".ps1", ".bat", ".cmd", ".xml", ".svg", ".sql",
                  ".tsv", ".log", ""}
# The harness notes after the model's own reply.
_NOTES = re.compile(r"\n\n(?:Verification(?: note)?:|Task-state note:|Smoke test:|Not verified:|Not completed:"
                    r"|Test-integrity note:|Incomplete:|Files written to:|Backend check:)")


# --- scrubbing -------------------------------------------------------------------------------------------------------
#
# Every replacement keeps the length of what it replaces. The trace clips long tool output at fixed lengths, and the
# verdict code reads what is left (the last lines, a summary cut at so many characters), so a shorter or longer path
# moves those cuts and the replay no longer sees what the run saw. Letters become x (capitals X), digits 0; separators,
# spaces, dots and case are kept, so a folder name with a space is still a folder name with a space.

def neutral(text: str) -> str:
    """`text` with the same length and shape, and none of its letters or digits."""
    return re.sub(r"[A-Za-z]", lambda m: "X" if m.group(0).isupper() else "x", re.sub(r"[0-9]", "0", text))


def _neutral_path(path: str) -> str:
    """A path made neutral, keeping its drive letter: "E:\\Some Folder" -> "E:\\Xxxx Xxxxxx"."""
    drive, rest = (path[:2], path[2:]) if re.match(r"^[A-Za-z]:", path) else ("", path)
    return drive + neutral(rest)


def _variants(path: str) -> list[str]:
    """A Windows path as it appears in text: plain, with doubled backslashes, with forward slashes."""
    return [path.replace("\\", "\\\\\\\\"), path.replace("\\", "\\\\"), path, path.replace("\\", "/")]


def scrubber(workspace: str, extra: dict[str, str]):
    """A function that replaces the run's workspace with <WS> and other local names with neutral ones.

    <WS> is the one replacement that changes length; a replay puts back a folder of the workspace's own length."""
    pairs = [(v, w) for v, w in zip(_variants(workspace), _variants(WS))]
    for local, replacement in extra.items():
        pairs += list(zip(_variants(local), _variants(replacement)))
    pairs.sort(key=lambda pair: -len(pair[0]))
    # What exact paths miss: a name written in other case or quoting, an address on the local network, an e-mail.
    words = [(re.compile(re.escape(local), re.IGNORECASE), replacement) for local, replacement in extra.items()
             if "\\" not in local and "/" not in local and len(local) > 3]
    words.sort(key=lambda pair: -len(pair[0].pattern))
    words += _GENERIC

    def scrub(text):
        if isinstance(text, str):
            for local, replacement in pairs:
                text = text.replace(local, replacement)
            for pattern, replacement in words:
                text = pattern.sub(replacement, text)
            return text
        if isinstance(text, list):
            return [scrub(item) for item in text]
        if isinstance(text, dict):
            return {scrub(k): scrub(v) for k, v in text.items()}
        return text
    return scrub


_KEEP_USERS = re.compile(r"(?:user|public|default|x+)\b", re.IGNORECASE)


def _user_folder(match) -> str:
    name = match.group("name")
    return match.group("head") + (name if _KEEP_USERS.match(name) else neutral(name))


_GENERIC = [
    (re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
     lambda m: neutral(m.group(0))),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), lambda m: neutral(m.group(0))),
    # A user folder, also when a tool cut the name short ("C:\Users\jo" for "C:\Users\joan.smith").
    (re.compile(r"(?P<head>[A-Za-z]:(?:\\|/)+Users(?:\\|/)+)(?P<name>[\w.~-]+)", re.IGNORECASE), _user_folder),
    # A short (8.3) folder name, such as the one Windows gives a long user name.
    (re.compile(r"\b[A-Z0-9]{1,6}~\d(?:\.[A-Z0-9]{1,3})?\b"), lambda m: neutral(m.group(0))),
    # A run that printed its network settings (ipconfig): the DNS suffix, hardware and IPv6 addresses.
    (re.compile(r"(?P<head>DNS Suffix[ .]*: ?)(?P<name>[\w.-]+)", re.IGNORECASE),
     lambda m: m.group("head") + neutral(m.group("name"))),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), lambda m: re.sub(r"[0-9A-Fa-f]", "0", m.group(0))),
    (re.compile(r"\b(?:fe80|fd[0-9a-f]{2}|2[0-9a-f]{3}):[0-9a-f:%]{4,}\b", re.IGNORECASE),
     lambda m: re.sub(r"[0-9A-Fa-f]", "0", m.group(0))),
]


def _local_names(workspace: Path) -> dict[str, str]:
    """Local folders and names that appear in a run, mapped to neutral ones of the same length."""
    names = {}
    home = Path.home()
    labs = workspace
    while labs.parent != labs and " " not in labs.name:
        labs = labs.parent
    if " " in labs.name:
        # The first folder with a space, its first word alone (as a shell cuts it), and the folder below it.
        names[str(labs)] = _neutral_path(str(labs))
        names[str(labs).split(" ")[0]] = _neutral_path(str(labs).split(" ")[0])
        for word in labs.name.split(" "):
            if len(word) > 3:
                names[word] = neutral(word)
        below = workspace.relative_to(labs).parts
        if below:
            names[below[0]] = neutral(below[0])
    names[str(home)] = _neutral_path(str(home))
    names[home.name] = neutral(home.name)
    for part in re.split(r"[^A-Za-z0-9]", home.name):
        if len(part) > 3:
            names[part] = neutral(part)
    return names


# --- export ----------------------------------------------------------------------------------------------------------

def _text_replies(messages: list, answer: str) -> list[str]:
    """The model's earlier replies with no tool call in them: the ones the run checked before its final answer."""
    replies = []
    for message in messages:
        content = message.get("content")
        if message.get("role") != "assistant" or message.get("tool_calls") or not isinstance(content, str):
            continue
        if "✿FUNCTION✿" in content or not content.strip():
            continue
        replies.append(content[:6000])
    own = _own_reply(answer).strip()
    return [r for r in replies if r.strip() != own][-12:]


def _own_reply(answer: str) -> str:
    own = _NOTES.split(answer or "")[0]
    return re.sub(r"^NOT VERIFIED:.*?\n\n", "", own, flags=re.DOTALL)


_WROTE = re.compile(r"^(?:Wrote \d+ bytes to|Edited|Updated|Created) (?P<path>.+?)"
                    r"(?: \(from the shell's folder| with \d+ additions?\b|\s*$)", re.MULTILINE)


def _calls(steps: list) -> list[dict]:
    """The shell commands and the file writes, in the order the run made them."""
    calls = []
    for step in steps:
        if step.get("type") != "tool_calls":
            continue
        for tool in step.get("tools") or []:
            name = tool.get("name")
            args = tool.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            args = args or {}
            result = str(tool.get("result") or "")
            if tool.get("cached") or result == "(cached/repeat)":
                # A repeat the engine answered from its last output: nothing ran and the tracker never saw it.
                continue
            if name == "execute_shell":
                if len(result) > _MAX_RESULT:
                    result = result[:_MAX_RESULT // 2] + "\n[...]\n" + result[-_MAX_RESULT // 2:]
                calls.append({"tool": "execute_shell", "command": str(args.get("command") or ""), "result": result})
            elif name == "check_app" and args.get("path"):
                # The model's own page or script check: a pass settles an earlier failed check of that work.
                calls.append({"tool": "check_app", "path": str(args["path"]), "result": result[:_MAX_RESULT]})
            elif name in ("write_file", "edit_file") and not result.startswith("Error"):
                landed = _WROTE.search(result)
                calls.append({"tool": name, "path": landed.group("path").strip() if landed
                              else str(args.get("filename") or "")})
    return calls


def _workspace_files(workspace: Path) -> tuple[dict[str, str], list[str]]:
    """The text files kept whole, and the names of the others (binary, large, or inside a skipped folder), which a
    replay recreates as small placeholders so a check that a file exists still finds it."""
    files, others, total = {}, set(), 0
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace)
        skipped = _SKIP_PARTS & set(rel.parts)
        if skipped:
            # One placeholder for the folder (a .venv, a node_modules), not its thousands of files.
            top = next(i for i, part in enumerate(rel.parts) if part in skipped)
            others.add(PurePosixPath(*rel.parts[:top + 1]).as_posix() + "/")
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        if path.suffix.lower() not in _TEXT_SUFFIXES or size > _MAX_FILE or total + size > _MAX_FILES_TOTAL:
            others.add(rel.as_posix())
            continue
        try:
            files[rel.as_posix()] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            others.add(rel.as_posix())
            continue
        total += size
    return files, sorted(others)


def export(run: Path, case_id: str) -> dict:
    run = Path(run)
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    trace = json.loads(max((run / "trace").glob("*.json")).read_text(encoding="utf-8"))
    state = trace.get("trajectory_state") or {}
    steps = (trace.get("trace") or [{}])[0].get("steps", [])
    answer = next((s.get("content", "") for s in reversed(steps) if s.get("type") == "final_answer"), "")
    workspace = (run / "workspace").resolve()
    scrub = scrubber(str(workspace), _local_names(workspace))
    ws_prefix = str(workspace) + "\\"
    rel = lambda p: str(p)[len(ws_prefix):].replace("\\", "/") if str(p).startswith(ws_prefix) else None
    files, others = _workspace_files(workspace)
    smoke = []
    for path, output in (state.get("smoke_output") or {}).items():
        if rel(path) is not None:
            smoke.append({"path": rel(path), "output": output})
    case = {
        "id": case_id,
        "task": result.get("task"),
        "workspace_length": len(str(workspace)),
        "prompt": (run / "prompt.md").read_text(encoding="utf-8"),
        "reply": _own_reply(answer),
        "replies": _text_replies(trace.get("messages") or [], answer),
        "answer": answer,
        "calls": [dict(call, path=rel(call["path"]) or call["path"]) if "path" in call else call
                  for call in _calls(steps)],
        "written": [rel(p) for p in state.get("written_paths") or [] if rel(p) is not None],
        "files": files,
        "other_files": others,
        "smoke": smoke,
        "recorded": {
            "smoke_notes": state.get("smoke_notes") or [],
            "partial_files": [rel(p) or p for p in state.get("partial_files") or []],
            "subgoals_built": bool(state.get("subgoals_built")),
            "unmet_checks": state.get("unmet_checks") or [],
            "harness_verdict": result.get("harness_verdict"),
            "outcome": result.get("outcome"),
        },
        "grader": {"passed": (result.get("grader") or {}).get("passed"),
                   "score": (result.get("grader") or {}).get("score")},
    }
    return scrub(case)


def save(case: dict, folder: Path = CORPUS) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{case['id']}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(case, handle, ensure_ascii=False, sort_keys=True)
    return path


def load(case_id: str, folder: Path = CORPUS) -> dict:
    with gzip.open(folder / f"{case_id}.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def labels(folder: Path = CORPUS) -> dict:
    return json.loads((folder / "labels.json").read_text(encoding="utf-8"))


# --- replay ----------------------------------------------------------------------------------------------------------

# Python only: a Node script check runs `node --check`, which depends on whether node is installed where the test runs.
_SCRIPT_SUFFIXES = {".py"}


def _workspace_like(work: Path, length: int) -> Path:
    """A workspace folder under `work` whose path has the run's own length, so clipped output is cut where it was.

    The long form of `work`: a short (8.3) temp path would not match the paths the tracker keeps. When `work` is
    already longer than the run's workspace, the folder is as short as it can be and the replay may differ."""
    base = Path(work).resolve()
    room = length - len(str(base)) - 1
    return base / ("w" * room if room > 0 else "w")


def isolate(home: Path) -> None:
    """Default settings for the replay, never the developer's own: the engine reads its config when it is imported."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT8088_HOME"] = str(home)
    os.environ["AGENT8088_CONFIG"] = str(home / "no_such_config.txt")
    os.environ["AGENT8088_SANDBOX"] = "local"


def replay(case: dict, work: Path) -> dict:
    """The verdict the current code gives on a case: {"verdict", "reasons"}.

    Call `isolate` first (the test's engine fixture does the same) so the engine is loaded with default settings."""
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    if "agent8088.engine" not in sys.modules and "AGENT8088_CONFIG" not in os.environ:
        raise RuntimeError("call corpus.isolate(<scratch folder>) before the first replay")
    from agent8088 import app_check, engine, secret_scan, tool_output
    from agent8088 import completion as completion_checks
    from agent8088.trajectory import TrajectoryState

    workspace = _workspace_like(Path(work), case.get("workspace_length", 0))
    workspace.mkdir(parents=True, exist_ok=True)
    root = str(workspace)
    put = lambda text: text.replace(WS + "\\", root + "\\").replace(WS + "/", root + "/").replace(WS, root)
    for name, text in case["files"].items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(put(text), encoding="utf-8")
    for name in case.get("other_files", []):
        target = workspace / name
        if name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            (target / "placeholder").write_text("x", encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
    written = [str(workspace / name) for name in case["written"]]
    located = lambda path: str(Path(path) if Path(path).is_absolute() else workspace / path)

    state = TrajectoryState({}, goal=case["prompt"], message_count=1)
    # The run's own folder: a passing run of a script it wrote there settles an earlier failed check.
    state.data["workspace"] = root
    for call in case["calls"]:
        if call["tool"] == "check_app":
            path, result = located(put(call["path"])), put(call["result"])
            verdict = tool_output.detect_verdict(result)
            status = engine._check_app_status("check_app", result, verdict.status)
            operation = state.before_tool("check_app")
            state.after_tool(operation, result, failed=engine._plan_step_failed(result), blocked=False,
                             mutated=False, verdict=status, path=path)
            state.settle_check_app(result, path=path)
            continue
        if call["tool"] != "execute_shell":
            # A write: it changes the work, so a later run of the same command is a new check of it.
            operation = state.before_tool(call["tool"])
            state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True,
                             path=located(put(call["path"])))
            continue
        command, result = put(call["command"]), put(call["result"])
        args = {"command": command}
        verdict = tool_output.detect_verdict(result)
        status = engine._evidence_status("execute_shell", args, result, verdict.status, verdict.source)
        operation = state.before_tool("execute_shell")
        state.after_tool(operation, result, failed=engine._plan_step_failed(result), blocked=False, mutated=False,
                         verdict=status, command=command)
    state.data["written_paths"] = list(dict.fromkeys([*state.written_paths(), *written]))

    # The page/script check: scripts again from the output they gave when started; pages as recorded.
    redone = set()
    for entry in case["smoke"]:
        path = workspace / entry["path"]
        # A start output kept whole is judged again; a clipped one has lost the lines that decide it.
        if path.suffix.lower() not in _SCRIPT_SUFFIXES or not path.is_file() or "\n[...]\n" in entry["output"]:
            continue
        output = put(entry["output"])
        report = app_check.check_script(path, run=lambda _command, _timeout, output=output: output)
        if report.status == app_check.SKIPPED:
            continue
        state.record_smoke(str(path), report.summary(), report.failed, report.output,
                           passed=report.status == app_check.PASS,
                           unverified=report.status == app_check.UNAVAILABLE and report.ask)
        redone.add(path.name)
    kept = [note for note in case["recorded"]["smoke_notes"] if note.split(":", 1)[0] not in redone]
    state.data["smoke_notes"] = [*kept, *(state.data.get("smoke_notes") or [])]
    state.data["partial_files"] = [put(p) for p in case["recorded"]["partial_files"]]

    unmet = [u for u in case["recorded"]["unmet_checks"]
             if not str(u).lower().startswith(("a credential is written", "missing or empty files"))]
    hits = secret_scan.scan_files([p for p in written if Path(p).is_file()])
    if hits and not all(finding.weak for _p, _l, finding in hits):
        unmet.append("a credential is written in clear text in: "
                     + ", ".join(sorted({Path(p).name for p, _l, _f in hits})[:6]))
    reply = put(case["reply"])
    missing = completion_checks.missing_outputs(case["prompt"], reply, written, [workspace],
                                                mirror_roots=[workspace / "artifacts"])
    if missing:
        unmet.append("missing or empty files: " + ", ".join(missing[:6]))
    state.data["unmet_checks"] = unmet

    # The parts of the request. The run judged them at each text reply it checked, and a part met once stays met; a
    # run that ended before any reply was checked (a cap, the clock) never built them.
    if case["recorded"].get("subgoals_built", True):
        state.set_subgoals(completion_checks.extract_subgoals(case["prompt"],
                                                              sentences=engine.REQUIREMENT_EVIDENCE))
        evidence = completion_checks.deliverable_text(written, [workspace]) if engine.REQUIREMENT_EVIDENCE else None
        for text in [*(put(r) for r in case.get("replies", [])), reply]:
            pending = state.open_subgoals()
            if not pending:
                break
            state.mark_subgoals(completion_checks.unaddressed_subgoals(
                pending, text, written, evidence=evidence or None, ran=state.ran_commands(), roots=[workspace]))

    reasons = engine._not_verified_reasons(state, reply)
    return {"verdict": "not_verified" if reasons else "done", "reasons": reasons,
            "failing_check": str(state.data.get("failing_check") or "")[:300]}


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("run")
    ex.add_argument("--id", required=True)
    rp = sub.add_parser("replay")
    rp.add_argument("ids", nargs="*")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        print(save(export(Path(args.run), args.id)))
        return 0
    marks = labels()
    scratch = Path(tempfile.mkdtemp(prefix="corpus-home-"))
    isolate(scratch)
    for case_id in args.ids or sorted(marks):
        with tempfile.TemporaryDirectory() as work:
            got = replay(load(case_id), Path(work))
        want = marks.get(case_id, {}).get("expected", "?")
        flag = "ok " if got["verdict"] == want else "BAD"
        print(f"{flag} {case_id}: {got['verdict']} (label {want}) {'; '.join(got['reasons'])[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
