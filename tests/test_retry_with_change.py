"""Retry with change: the same failure after a fix means the fix did not address the cause.

A check that fails, then fails the same way again after the model changed files, is a fix that missed. Repeating the
same kind of fix costs turns, so the controller notices it (same command, same error line with numbers ignored) and
says so once per repeat: in the tool result, and in the ledger until a check passes. A failure that changes after
the edit is progress, not a repeat; failing twice with nothing changed in between is not a repeat of a fix either.
"""
import pytest

from agent8088.trajectory import TrajectoryState

FAIL = "FAILED tests/test_calc.py::test_add - assert 3 == 2\n1 failed in 0.11s\nCommand exited with status 1."
OTHER = "FAILED tests/test_calc.py::test_sub - NameError: name 'sub' is not defined\n1 failed in 0.09s\nCommand exited with status 1."
PASS = "2 passed in 0.10s"


@pytest.fixture
def state(tmp_path):
    return TrajectoryState({}, goal="fix the calculator", message_count=1, workspace=str(tmp_path))


def _edit(s, tmp_path):
    op = s.before_tool("edit_file")
    s.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(tmp_path / "calc.py"))


def _run(s, result, verdict):
    op = s.before_tool("execute_shell")
    s.after_tool(op, result, failed=verdict == "failed", blocked=False, mutated=False, verdict=verdict,
                 command="python -m pytest -q")


def test_the_same_failure_after_a_fix_is_a_repeat(state, tmp_path):
    _run(state, FAIL, "failed")
    assert state.repeated_failure() == 0
    _edit(state, tmp_path)
    _run(state, FAIL.replace("0.11s", "0.13s"), "failed")      # timings differ; the failure is the same
    assert state.repeated_failure() == 1
    _edit(state, tmp_path)
    _run(state, FAIL, "failed")
    assert state.repeated_failure() == 2


def test_a_different_failure_is_progress(state, tmp_path):
    _run(state, FAIL, "failed")
    _edit(state, tmp_path)
    _run(state, OTHER, "failed")
    assert state.repeated_failure() == 0


def test_failing_again_with_nothing_changed_is_not_a_failed_fix(state):
    _run(state, FAIL, "failed")
    _run(state, FAIL, "failed")
    assert state.repeated_failure() == 0


def test_a_pass_clears_it(state, tmp_path):
    _run(state, FAIL, "failed")
    _edit(state, tmp_path)
    _run(state, FAIL, "failed")
    _run(state, PASS, "passed")
    assert state.repeated_failure() == 0
    assert "same way" not in state.state_note()


def test_the_ledger_names_a_repeat(state, tmp_path):
    _run(state, FAIL, "failed")
    _edit(state, tmp_path)
    _run(state, FAIL, "failed")
    note = state.state_note()
    assert "failed the same way after 1 change(s)" in note and "different approach" in note


def test_the_engine_says_it_once_per_repeat(engine, state, tmp_path):
    _run(state, FAIL, "failed")
    assert engine._repeat_failure_note(state) == ""
    _edit(state, tmp_path)
    _run(state, FAIL, "failed")
    note = engine._repeat_failure_note(state)
    assert "failed the same way after your change" in note and "different approach" in note
    assert engine._repeat_failure_note(state) == ""      # said once for this repeat
    _edit(state, tmp_path)
    _run(state, FAIL, "failed")
    assert "2 times" in engine._repeat_failure_note(state)
