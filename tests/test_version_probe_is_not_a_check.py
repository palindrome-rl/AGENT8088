"""Asking a tool for its version or help text is not a check: its exit status must not leave the run
marked as having a failing check."""
import pytest

from agent8088.trajectory import TrajectoryState, _is_probe


def _record(state, verdict, command, result="x"):
    op = state.before_tool("execute_shell")
    state.after_tool(op, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


@pytest.mark.parametrize("command", [
    "python -m pytest --version",
    "python -m pytest --version 2>&1",
    "pytest --help",
    "npm test -V",
    r"cd E:\app && python -m pytest --version",
    "python -m pytest --version & echo done",
    "node --version && npm --version",
])
def test_these_only_ask_for_a_version_or_help_text(command):
    assert _is_probe(command)


@pytest.mark.parametrize("command", [
    "",
    "python -m pytest",
    "python -m pytest -q tests",
    "python -m pytest --version && python -m pytest -q",
    "python -m py_compile app.py",
    "pytest --verbose",
    "pytest -v",
    "python -m pytest -v",
    "echo done",
    r"cd E:\app",
])
def test_these_do_real_work_or_nothing(command):
    assert not _is_probe(command)


def test_a_failed_version_probe_does_not_mark_the_run_as_failing():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "failed", "python -m pytest --version 2>&1", result="No module named pytest")
    assert not state.check_failing()


def test_a_failed_test_run_still_does():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "failed", "python -m pytest -q", result="1 failed, 2 passed")
    assert state.check_failing()


def test_a_failed_verbose_test_run_is_not_a_version_probe():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "failed", "python -m pytest -v", result="1 failed, 2 passed")
    assert state.check_failing()


def test_a_probe_does_not_clear_a_real_failure_or_count_as_a_pass():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "failed", "python -m pytest -q", result="1 failed")
    _record(state, "failed", "python -m pytest --version", result="No module named pytest")
    assert state.check_failing()
    assert "pytest -q" in state.failing_check_detail()
