"""A repair attempt must not turn failing-check evidence into success."""
import pytest

from agent8088.trajectory import TrajectoryState


def record(state, tool, verdict="unknown", mutated=False):
    operation = state.before_tool(tool)
    state.after_tool(operation, verdict, failed=verdict == "failed",
                     blocked=False, mutated=mutated, verdict=verdict)


@pytest.mark.parametrize("mutating_check", [False, True])
def test_failed_check_remains_after_an_unverified_repair(mutating_check):
    state = TrajectoryState({}, goal="repair", message_count=1)
    record(state, "run_tests", "failed", mutating_check)
    assert state.check_failing()
    record(state, "write_file", mutated=True)
    record(state, "read_text")
    assert state.check_failing()
    record(state, "run_tests", "passed")
    assert not state.check_failing()


def test_failed_check_persists_when_the_session_state_is_reloaded():
    saved = {}
    state = TrajectoryState(saved, goal="repair", message_count=1)
    record(state, "run_tests", "failed")
    record(state, "write_file", mutated=True)
    resumed = TrajectoryState(saved, goal="continue repair", message_count=2)
    assert resumed.check_failing()
    record(resumed, "run_tests", "passed")
    assert not resumed.check_failing()
