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
    specs = {"execute_shell": {"description": "Run a command."}}
    engine._ground_execute_shell_description(specs)
    assert f"Working directory: {launch} (the configured {missing} does not exist)" \
        in specs["execute_shell"]["description"]


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
