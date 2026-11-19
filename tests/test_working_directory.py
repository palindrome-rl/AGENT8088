"""A configured shell_cwd or project_root that does not exist here (a path from
another machine or container image) must not fail every command with an error
that reads like a missing program: commands fall back to an allowed directory,
or say plainly that the working directory is the problem."""
import json
import os
from pathlib import Path

import pytest

posix_only = pytest.mark.skipif(os.name == "nt", reason="uses POSIX pwd")


def _host_shell(engine):
    engine.TOOL_SPECS["execute_shell"]["host"] = "1"  # bash on the host, no sandbox


def _layout(engine, monkeypatch, tmp_path, allowed=None):
    """shell_cwd points at a missing /app-like folder; the launch dir exists."""
    launch = tmp_path / "workspace"
    launch.mkdir()
    missing = tmp_path / "app"
    monkeypatch.setattr(engine, "SHELL_CWD", missing)
    monkeypatch.setattr(engine, "LAUNCH_DIR", launch)
    monkeypatch.setattr(engine, "PROJECT_ROOT", launch)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [allowed or launch])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    return launch, missing


@posix_only
def test_missing_shell_cwd_falls_back_to_the_launch_directory(engine, monkeypatch, tmp_path):
    _host_shell(engine)
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    first = engine.exec_tool("execute_shell", json.dumps({"command": "pwd"}))
    assert str(launch.resolve()) in first
    assert f"working directory {missing} does not exist" in first
    assert f"Commands now run in {launch}" in first
    second = engine.exec_tool("execute_shell", json.dumps({"command": "pwd"}))
    assert str(launch.resolve()) in second
    assert "does not exist" not in second  # told once, not on every command


def test_no_allowed_fallback_reports_the_working_directory(engine, monkeypatch, tmp_path):
    _host_shell(engine)
    elsewhere = tmp_path / "only-allowed"
    elsewhere.mkdir()
    _, missing = _layout(engine, monkeypatch, tmp_path, allowed=elsewhere)
    result = engine.exec_tool("execute_shell", json.dumps({"command": "ls"}))
    assert '"code": "working_directory_missing"' in result
    assert str(missing) in result
    assert "shell_cwd" in result
    assert "executable" not in result and "File not found" not in result


def test_fallback_never_lands_in_a_blocked_path(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [launch])
    assert engine._choose_shell_cwd() is None


def test_existing_shell_cwd_is_used_as_configured(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    configured = tmp_path / "configured"
    configured.mkdir()
    monkeypatch.setattr(engine, "SHELL_CWD", configured)
    assert engine._shell_cwd() == configured
    assert engine._take_cwd_note(sandboxed=False) == ""


def test_sandboxed_note_names_the_folder_commands_really_run_in(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", launch / "artifacts")
    assert engine._shell_cwd() == launch
    note = engine._take_cwd_note(sandboxed=True)
    # The sandbox moves every command into artifacts/; the note must match pwd.
    assert f"working directory {missing} does not exist" in note
    assert f"Commands now run in {launch / 'artifacts'}." in note
    assert engine._take_cwd_note(sandboxed=True) == ""  # still only once


def test_missing_program_in_a_valid_directory_is_still_not_found(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    error = FileNotFoundError(2, "No such file or directory", "no-such-program-xyz")
    result = engine._not_found_tool_error(error)
    assert '"code": "not_found"' in result
    assert "no-such-program-xyz" in result


def test_directory_vanishing_at_start_is_reported_as_the_directory(engine, monkeypatch, tmp_path):
    _, missing = _layout(engine, monkeypatch, tmp_path)
    # What Popen raises when its cwd is missing: the directory as the filename.
    error = FileNotFoundError(2, "No such file or directory", str(missing))
    assert '"code": "working_directory_missing"' in engine._not_found_tool_error(error)


def test_missing_project_root_falls_back_to_the_launch_directory(engine, tmp_path):
    launch = tmp_path / "workspace"
    launch.mkdir()
    chosen = engine._configured_project_root({"project_root": str(tmp_path / "app")}, launch)
    assert chosen == launch.resolve()


def test_existing_project_root_is_kept(engine, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    launch = tmp_path / "workspace"
    launch.mkdir()
    assert engine._configured_project_root({"project_root": str(root)}, launch) == root.resolve()


def test_missing_project_root_still_honours_allowed_paths(engine, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    launch = tmp_path / "workspace"
    launch.mkdir()
    chosen = engine._configured_project_root(
        {"project_root": str(tmp_path / "app"), "allowed_paths": str(allowed)}, launch)
    assert chosen == allowed.resolve()


def test_tool_description_names_the_directory_actually_used(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    specs = {"execute_shell": {"description": "Run a command.", "host": "1"}}
    engine._ground_execute_shell_description(specs)
    assert f"Working directory: {launch} (the configured {missing} does not exist)" \
        in specs["execute_shell"]["description"]


def test_sandboxed_tool_description_names_the_sandbox_workspace(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", launch / "artifacts")
    specs = {"execute_shell": {"description": "Run a command."}}
    engine._ground_execute_shell_description(specs)
    assert (f"Working directory: {launch / 'artifacts'} (the sandbox workspace; "
            f"project files are under {launch})") in specs["execute_shell"]["description"]


def test_scheduled_tasks_start_in_the_folder_commands_use(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    assert engine._scheduled_task_cwd() == launch
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path / "nowhere"])
    assert engine._scheduled_task_cwd() == missing   # nothing usable: name the setting


def test_doctor_row_warns_on_a_fallback_and_fails_without_one(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    status, detail, fix = engine.working_directory_status()
    assert status == "warn"
    assert f"shell_cwd {missing} does not exist" in detail and str(launch) in detail
    assert "shell_cwd" in fix

    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path / "nowhere"])
    status, _, fix = engine.working_directory_status()
    assert status == "fail"
    assert "allowed_paths" in fix

    monkeypatch.setattr(engine, "SHELL_CWD", launch)
    assert engine.working_directory_status()[0] == "ok"


# --- B3.2: a folder that exists but cannot be entered; the one that vanished --

needs_permissions = pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission bits; root can enter any folder")


@needs_permissions
def test_unenterable_shell_cwd_falls_back_and_says_why(engine, monkeypatch, tmp_path):
    _host_shell(engine)
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    missing.mkdir()
    missing.chmod(0)
    try:
        first = engine.exec_tool("execute_shell", json.dumps({"command": "pwd"}))
    finally:
        missing.chmod(0o755)
    assert str(launch.resolve()) in first
    assert f"working directory {missing} cannot be entered (permission denied)" in first


@posix_only
def test_read_only_shell_cwd_is_still_used(engine, monkeypatch, tmp_path):
    _host_shell(engine)
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    read_only.chmod(0o555)
    monkeypatch.setattr(engine, "SHELL_CWD", read_only)
    try:
        result = engine.exec_tool("execute_shell", json.dumps({"command": "pwd"}))
    finally:
        read_only.chmod(0o755)
    # Commands may start in a folder they cannot write to; that is not a
    # reason to move them, and nothing is said about it.
    assert str(read_only.resolve()) in result
    assert "does not exist" not in result and "cannot be entered" not in result


def test_vanished_fallback_is_named_not_the_configured_folder(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    launch.rmdir()  # the fallback in use disappears between the check and the start
    error = FileNotFoundError(2, "No such file or directory", str(launch))
    result = engine._not_found_tool_error(error)
    assert '"code": "working_directory_missing"' in result
    assert f"The working directory {launch} does not exist" in result


# --- B3.3: environment probe in the tool description ------------------------

def test_probe_lists_visible_names_capped(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    for i in range(15):
        (launch / f"file{i:02d}.txt").write_text("x")
    (launch / ".env").write_text("x")
    line = engine._environment_probe(launch)
    assert "file00.txt" in line and "file07.txt" in line
    assert "file08.txt" not in line and "(+7 more)" in line
    assert ".env" not in line  # hidden names are not listed, as with plain ls


def test_probe_does_not_list_a_folder_outside_allowed_paths(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path, allowed=tmp_path / "elsewhere")
    (launch / "secret-plan.txt").write_text("x")
    line = engine._environment_probe(launch)
    assert "secret-plan.txt" not in line
    assert "outside allowed_paths" in line


def test_probe_redacts_secret_values(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    secret = "sk-test-0123456789abcdef"
    (launch / f"{secret}.txt").write_text("x")
    monkeypatch.setattr(engine, "_SECRET_VALUES", [secret])
    line = engine._environment_probe(launch)
    assert secret not in line and "[redacted]" in line


def test_probe_names_user_and_interpreters_and_is_bounded(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    for i in range(12):
        (launch / ("n" * 60 + str(i))).write_text("x")
    monkeypatch.setattr(engine, "_probe_user", lambda: "builder")
    monkeypatch.setattr(engine.shutil, "which",
                        lambda name: "/usr/bin/git" if name == "git" else None)
    line = engine._environment_probe(launch)
    assert "user builder" in line
    assert "on PATH: git" in line and "not found:" in line and "node" in line
    assert len(line) <= 400


def test_description_carries_the_probe_and_is_not_stacked(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    (launch / "data.csv").write_text("1\n")
    specs = {"execute_shell": {"description": "Run a command."}}
    engine._ground_execute_shell_description(specs)
    engine._ground_execute_shell_description(specs)
    description = specs["execute_shell"]["description"]
    assert "At session start the folder contains" in description and "data.csv" in description
    assert description.count("Working directory:") == 1


def test_grounding_refreshes_only_when_the_folder_changes(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    missing.mkdir()
    monkeypatch.setitem(engine.TOOL_SPECS, "execute_shell",
                        dict(engine.TOOL_SPECS["execute_shell"]))
    _host_shell(engine)  # a sandboxed shell reports the sandbox workspace instead
    engine._ground_execute_shell_description(engine.TOOL_SPECS)
    before = engine.TOOLS_DEF
    (missing / "new-file.txt").write_text("x")  # contents change: no rewrite
    assert engine.refresh_shell_grounding() is False
    assert engine.TOOLS_DEF is before
    (missing / "new-file.txt").unlink()
    missing.rmdir()  # the folder itself goes away mid-session
    assert engine.refresh_shell_grounding() is True
    description = engine.TOOL_SPECS["execute_shell"]["description"]
    assert f"Working directory: {launch} (the configured {missing} does not exist)" in description
    assert description.count("Working directory:") == 1
    assert engine.TOOLS_DEF is not before


# --- B3.4: diagnostic circuit breaker ---------------------------------------

@pytest.mark.parametrize("answer", [
    "The environment is inaccessible, so I cannot continue.",
    "I wasn't able to complete either part of the request… the workspace itself appears to be missing.",
    "It seems the sandbox is not available right now.",
    "I cannot access the filesystem in this session.",
    "Unable to run commands in the working directory.",
    "The shell environment appears to be broken.",
])
def test_environment_claims_are_recognised(engine, answer):
    assert engine._claims_environment_unavailable(answer)


@pytest.mark.parametrize("answer", [
    "The sum of the numbers in data.csv is 6.",
    "I created the file in the workspace and ran the tests; all pass.",
    "The API endpoint returned 503, so the service is unavailable.",
])
def test_ordinary_answers_are_not_environment_claims(engine, answer):
    assert not engine._claims_environment_unavailable(answer)


def test_tool_error_code(engine):
    assert engine._tool_error_code(engine._missing_cwd_error()) == "working_directory_missing"
    assert engine._tool_error_code("Error: something broke") == "error"
    assert engine._tool_error_code("total 0\nfile.txt") is None
    assert engine._tool_error_code(None) is None


def test_diagnostic_reports_status_listing_and_space(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    (launch / "data.csv").write_text("1\n")
    text, status = engine.environment_diagnostic()
    assert status == "warn"
    assert f"shell_cwd {missing} does not exist" in text
    assert "data.csv" in text and "free disk space" in text
    assert len(text) <= 1500

    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path / "nowhere"])
    text, status = engine.environment_diagnostic()
    assert status == "fail" and "no allowed folder" in text


def _response(content):
    message = type("M", (), {"content": content, "tool_calls": None})()
    choice = type("C", (), {"message": message, "finish_reason": "stop"})()
    return type("R", (), {"choices": [choice]})()


def _run_script(engine, monkeypatch, replies, max_turns=8):
    """Drive the real loop with scripted model replies; returns (answer, the
    message list each model call saw, trace)."""
    seen = []

    def fake(messages, tools, **kwargs):
        seen.append([str(m.get("content")) for m in messages])
        return _response(replies[min(len(seen) - 1, len(replies) - 1)])

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
    _host_shell(engine)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "sum data.csv"}],
                              max_turns=max_turns, trace=trace, memory_capture=False)
    return answer, seen, trace


def _shell(command):
    return f'✿FUNCTION✿: execute_shell ✿ARGS✿: {{"command": "{command}"}}'


def _no_fallback(engine, monkeypatch, tmp_path):
    elsewhere = tmp_path / "only-allowed"
    elsewhere.mkdir()
    return _layout(engine, monkeypatch, tmp_path, allowed=elsewhere)


def test_repeated_identical_failures_bring_a_diagnostic(engine, monkeypatch, tmp_path):
    _no_fallback(engine, monkeypatch, tmp_path)
    answer, seen, trace = _run_script(engine, monkeypatch, [
        _shell("ls"), _shell("pwd"), "The working directory setting is wrong; set shell_cwd."])
    results = [c for c in seen[-1] if c.startswith("Tool result (execute_shell")]
    assert len(results) == 2
    assert "read-only check" not in results[0]
    assert "failed the same way (working_directory_missing) 2 times" in results[1]
    assert "working directory: fail" in results[1]
    runs = [s for s in trace if s.get("type") == "diagnostic_run"]
    assert [r["trigger"] for r in runs] == ["repeated_failure"]
    assert runs[0]["code"] == "working_directory_missing" and runs[0]["outcome"] == "fail"
    missing_events = [s for s in trace if s.get("type") == "working_directory_missing"]
    assert len(missing_events) == 2 and missing_events[0]["tool"] == "execute_shell"


def test_unchecked_environment_claim_gets_one_diagnostic_turn(engine, monkeypatch, tmp_path):
    _no_fallback(engine, monkeypatch, tmp_path)
    claim = "The environment is inaccessible, so I cannot do this."
    answer, seen, trace = _run_script(engine, monkeypatch, [
        _shell("ls"), claim, "Commands cannot start: shell_cwd points at a missing folder."])
    assert answer.startswith("Commands cannot start")
    nudges = [c for c in seen[-1] if "You concluded that the environment cannot be used" in c]
    assert len(nudges) == 1 and "working directory: fail" in nudges[0]
    runs = [s for s in trace if s.get("type") == "diagnostic_run"]
    assert [r["trigger"] for r in runs] == ["environment_claim"]


def test_claim_after_a_diagnostic_is_accepted(engine, monkeypatch, tmp_path):
    _no_fallback(engine, monkeypatch, tmp_path)
    claim = "The environment is inaccessible: the check shows no allowed folder."
    answer, seen, trace = _run_script(engine, monkeypatch, [_shell("ls"), _shell("pwd"), claim])
    assert answer.startswith(claim)
    # No environment-claim nudge: the diagnostic was already shown. (The
    # controller's own replan nudge after two failures may still add a turn.)
    assert not any("You concluded that the environment" in c for turn in seen for c in turn)
    assert [s["trigger"] for s in trace if s.get("type") == "diagnostic_run"] == ["repeated_failure"]


def test_claim_without_any_failed_tool_is_left_alone(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    claim = "The sandbox is not available in this chat, so here is the plan instead."
    answer, seen, trace = _run_script(engine, monkeypatch, [claim])
    assert answer.startswith(claim) and len(seen) == 1
    assert not [s for s in trace if s.get("type") == "diagnostic_run"]


@posix_only
def test_fallback_is_traced_as_cwd_repaired(engine, monkeypatch, tmp_path):
    launch, missing = _layout(engine, monkeypatch, tmp_path)
    answer, seen, trace = _run_script(engine, monkeypatch, [_shell("pwd"), "done"])
    repaired = [s for s in trace if s.get("type") == "cwd_repaired"]
    assert len(repaired) == 1
    assert repaired[0]["from"] == str(missing) and repaired[0]["to"] == str(launch)


def test_different_failures_do_not_build_a_streak(engine, monkeypatch, tmp_path):
    _layout(engine, monkeypatch, tmp_path)
    answer, seen, trace = _run_script(engine, monkeypatch, [
        _shell("no-such-program-xyz"),
        '✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "missing.txt"}',
        "done"])
    assert not [s for s in trace if s.get("type") == "diagnostic_run"]


# --- B4: a real process started with a missing shell_cwd --------------------

@posix_only
def test_agent_process_with_missing_cwd_runs_its_first_command(tmp_path):
    """Integration: a fresh interpreter, a config naming /app-like folders that
    do not exist, launched from a different folder — the way a container image
    or an installer default meets a machine laid out differently."""
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.csv").write_text("1\n2\n3\n")
    missing = tmp_path / "app"
    config = home / "config.txt"
    config.write_text(f"shell_cwd={missing}\nproject_root={missing}\n", encoding="utf-8")
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(root / 'src')!r})\n"
        "from agent8088 import engine\n"
        "engine.TOOL_SPECS['execute_shell']['host'] = '1'\n"
        "print('ROOT=' + str(engine.PROJECT_ROOT))\n"
        "print(engine.exec_tool('execute_shell', json.dumps({'command': 'pwd && ls'})))\n"
    )
    # Nothing inherited from earlier tests (a provider pinned in os.environ
    # prints its own banner line) — only what this process is meant to see.
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT8088_")}
    env.update(HOME=str(home), AGENT8088_HOME=str(home),
               AGENT8088_CONFIG=str(config), AGENT8088_SANDBOX="local",
               ANONYMIZED_TELEMETRY="false", POSTHOG_DISABLED="1")
    done = subprocess.run([sys.executable, "-c", script], cwd=workspace, env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    out = done.stdout
    root_line = next(line for line in out.splitlines() if line.startswith("ROOT="))
    assert root_line == f"ROOT={workspace.resolve()}"  # project_root fell back
    assert "data.csv" in out
    assert f"The configured working directory {missing.resolve()} does not exist" in out


def test_probe_does_not_list_a_blocked_folder(engine, monkeypatch, tmp_path):
    launch, _ = _layout(engine, monkeypatch, tmp_path)
    (launch / "private.txt").write_text("x")
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [launch])
    line = engine._environment_probe(launch)
    assert "private.txt" not in line and "in blocked_paths" in line
