"""Verify _set_step and prompt() survive a trajectory dict with no checklist."""

from agent8088.trajectory import TrajectoryState


def _bare_state():
    """Minimal dict that passes _normalise but has no checklist key."""
    state = {
        "version": 2,
        "goal": "test",
        "workspace": "",
        "operations": [],
        "evidence": [],
        "mutation_count": 0,
        "verification": "not_needed",
        "verification_requested": False,
        "untested_changes": [],
        "tests_requested": False,
        "consecutive_setbacks": 0,
        "stalled": False,
        "replan_requested": False,
        "message_count": 0,
    }
    return state


def test_set_step_survives_missing_checklist():
    """_set_step must not crash when checklist is absent from the dict."""
    state = _bare_state()
    t = TrajectoryState(state, goal="test")
    # Force-remove checklist to simulate concurrent clear from another request
    t.data.pop("checklist", None)
    # Should be a no-op, not a KeyError
    t._set_step("Inspect the current state", "done")


def test_prompt_survives_missing_checklist():
    """prompt() must not crash when checklist is absent."""
    state = _bare_state()
    state["operations"] = [{"tool": "read", "status": "done"}]
    t = TrajectoryState(state, goal="test")
    t.data.pop("checklist", None)
    result = t.prompt()
    assert isinstance(result, str)
