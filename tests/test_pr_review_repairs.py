"""Regressions reproduced while reviewing PRs #284-#286 (S40/S39/S41)."""
from contextlib import nullcontext
from pathlib import Path
import socket

import pytest

from agent8088 import app_check, completion
from agent8088.trajectory import TrajectoryState
from test_issue_sheet_39b import _local_runner


FAKE_KEY = "sk-proj-AbCdEf0123456789AbCdEf0123456789"


@pytest.mark.parametrize("redirected", [False, True])
@pytest.mark.parametrize("cap", [False, True])
def test_scan_uses_the_written_workspace_not_cwd(engine, monkeypatch, tmp_path, redirected, cap):
    project, cwd = tmp_path / "project", tmp_path / "cwd"
    project.mkdir()
    cwd.mkdir()
    artifacts = project / "artifacts" if redirected else project
    artifacts.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [project])
    target = artifacts / "notes.txt"
    target.write_text(FAKE_KEY, encoding="utf-8")
    # A harmless namesake in the process directory must not hide the real output.
    (cwd / "notes.txt").write_text("clean namesake", encoding="utf-8")
    state = TrajectoryState(None)
    state.after_tool(state.before_tool("write_file"), "Wrote file", failed=False,
                     blocked=False, mutated=True, path="notes.txt",
                     written_to=str(target) if redirected else "")
    if cap:
        engine._record_unmet_checks(completion.ContinuationBudget(2), state,
                                   reply="Done", request="Save a note", depth=0)
        assert any("credential" in text for text in state.data["unmet_checks"])
    else:
        nudge = engine._secrets_step(state, state.written_paths())
        assert nudge is not None and "credential" in nudge.unmet
        assert FAKE_KEY not in nudge.text


def test_an_unreadable_file_is_not_retired_as_clean(engine, monkeypatch, tmp_path):
    target = tmp_path / "later.txt"
    target.write_text(FAKE_KEY, encoding="utf-8")
    real_read = Path.read_text
    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError("temporarily inaccessible")
        return real_read(path, *args, **kwargs)
    state = TrajectoryState(None)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_text", denied)
        assert engine._secrets_step(state, [str(target)]) is None
    assert state.secrets_unchecked([str(target)]) == [str(target)]
    assert engine._secrets_step(state, [str(target)]) is not None


def test_an_unrelated_listener_cannot_pass_the_boot_probe(tmp_path):
    marker = tmp_path / "started.txt"
    with socket.socket() as unrelated:
        unrelated.bind(("127.0.0.1", 0))
        unrelated.listen()
        port = unrelated.getsockname()[1]
        script = tmp_path / "server.py"
        script.write_text(
            "from pathlib import Path\nimport time\nfrom http.server import HTTPServer\n"
            f"Path({str(marker)!r}).write_text('started')\ntime.sleep(2)\n"
            f"HTTPServer(('127.0.0.1', {port}), None).serve_forever()\n", encoding="utf-8")
        report = app_check.check_script(script, run=_local_runner(tmp_path))
        assert report.status == app_check.UNAVAILABLE, report.text()
        assert "already" in report.note and "not verified" in report.note
        assert not marker.exists(), "do not start an app when another service owns its port"


@pytest.fixture
def planning(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(engine, "_human_wait", nullcontext)
    monkeypatch.setattr(engine, "_plan_on_approval", lambda text: None)
    return engine


def test_rejected_plan_preserves_existing_file_and_discloses_proposal(planning, tmp_path):
    original = tmp_path / "PLAN.md"
    original.write_text("User's original plan\n", encoding="utf-8")
    result = planning._exec_present_plan({"plan": "A rejected proposal"})
    assert original.read_text() == "User's original plan\n"
    proposals = list(tmp_path.glob("PLAN.proposal-*.md"))
    assert len(proposals) == 1
    assert proposals[0].read_text() == "A rejected proposal\n"
    assert str(proposals[0]) in result
    assert "Nothing has been written" not in result


@pytest.mark.parametrize("guard", ["allowed", "blocked", "sensitive", "startup"])
def test_plan_save_obeys_workspace_guards(planning, monkeypatch, tmp_path, guard):
    asked = []
    monkeypatch.setattr(planning, "_plan_on_approval", lambda text: asked.append(text))
    if guard == "allowed":
        monkeypatch.setattr(planning, "ALLOWED_PATHS", [tmp_path / "elsewhere"])
    elif guard == "blocked":
        monkeypatch.setattr(planning, "BLOCKED_PATHS", [tmp_path])
    elif guard == "sensitive":
        monkeypatch.setattr(planning, "_is_sensitive_path", lambda path: True)
    else:
        monkeypatch.setattr(planning, "_is_shell_startup_file", lambda path: True)
    result = planning._exec_present_plan({"plan": "Do not save outside permitted paths"})
    assert result.startswith("Error:")
    assert not list(tmp_path.glob("*.md"))
    assert not asked, "do not request approval for a plan that could not be saved"


def test_plan_does_not_follow_an_existing_link(planning, tmp_path):
    target = tmp_path / "important.txt"
    target.write_text("keep", encoding="utf-8")
    # A hard link works on Windows without developer-mode symlink privileges.
    (tmp_path / "PLAN.md").hardlink_to(target)
    planning._exec_present_plan({"plan": "New proposal"})
    assert target.read_text() == "keep"
    assert list(tmp_path.glob("PLAN.proposal-*.md"))


def test_plan_creation_race_cannot_replace_another_writers_file(planning, monkeypatch, tmp_path):
    real_open = Path.open
    original = tmp_path / "PLAN.md"
    def competing_write(path, mode="r", *args, **kwargs):
        if path == original and mode == "x":
            with real_open(path, "w", encoding="utf-8") as competing:
                competing.write("another writer's plan")
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", competing_write)
    result = planning._exec_present_plan({"plan": "Our proposal"})
    assert original.read_text() == "another writer's plan"
    assert "Proposal saved at" in result
    assert list(tmp_path.glob("PLAN.proposal-*.md"))


def test_cli_plan_rejection_does_not_deny_saving_the_proposal(monkeypatch):
    from agent8088 import cli
    shown = []
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: "d")
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: shown.extend(str(item) for item in a))
    assert cli._make_plan_approval()("A proposal") == ""
    assert any("No plan steps were run" in line for line in shown)
    assert not any("Nothing was written" in line for line in shown)
