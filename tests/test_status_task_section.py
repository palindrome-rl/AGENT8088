"""/status also shows what the harness recorded about the current task: goal, plan steps, changed files, verification level and the latest
check. It is read from the saved state with no model call, so it can be asked at any time (for example after an interrupt) and never
changes the run."""
import copy
from types import SimpleNamespace

from agent8088.trajectory import TrajectoryState


def _state_after_a_change():
    state = TrajectoryState({}, goal="Add the storm arcs to the globe", message_count=1)
    state.data["written_paths"] = ["C:\\w\\globe.js", "C:\\w\\arcs.js"]
    operation = state.before_tool("write_file")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path="C:\\w\\globe.js")
    return state


def test_no_task_no_rows():
    assert TrajectoryState({}, goal="", message_count=0).status_lines() == []


def test_a_changed_task_reports_goal_files_and_level():
    rows = dict(_state_after_a_change().status_lines())
    assert rows["Task"].startswith("Add the storm arcs")
    assert "globe.js" in rows["Changed"] and "arcs.js" in rows["Changed"]
    assert rows["Verification"] in {"unchecked", "inspected", "exercised", "tested", "failed"}
    assert rows["Latest check"] == "none passed yet"


def test_a_failing_check_is_named():
    state = _state_after_a_change()
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, "1 failed", failed=False, blocked=False, mutated=False, verdict="failed", command="pytest")
    rows = dict(state.status_lines())
    assert rows["Latest check"].startswith("FAILING")
    assert rows["Verification"] == "failed"


def test_a_passed_check_is_counted():
    state = _state_after_a_change()
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, "3 passed", failed=False, blocked=False, mutated=False, verdict="passed", command="pytest")
    assert "passed" in dict(state.status_lines())["Latest check"]


def test_a_long_goal_is_shortened():
    state = TrajectoryState({}, goal="x" * 300, message_count=1)
    assert len(dict(state.status_lines())["Task"]) <= 90


def test_the_cli_rows_do_not_change_the_saved_state(monkeypatch):
    from agent8088 import cli
    saved = _state_after_a_change().snapshot()
    before = copy.deepcopy(saved)
    monkeypatch.setattr(cli, "S", SimpleNamespace(trajectory_state=saved, messages=[{"role": "user", "content": "x"}] * 3))
    rows = dict(cli._task_status_rows())
    assert "Task" in rows
    assert saved == before


def test_a_damaged_state_gives_no_rows_and_no_crash(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli, "S", SimpleNamespace(trajectory_state="not a dict", messages=[]))
    assert cli._task_status_rows() == []
