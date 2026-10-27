"""A passing page check after the last edit ends the "pending" state, so the footer no longer contradicts itself.

Reported by a teammate: the end-of-turn footer said "Verification note: changes were made, but no fresh automated verification
evidence was recorded" right after tests and check_app had passed. With tests passing, then another edit, then only a passing
check_app, the level read "exercised" (the page ran without an error) while the work was still marked pending, so the note
contradicted the line above it, and the model was asked once more to verify."""
from agent8088.trajectory import TrajectoryState

APP = "C:\\w\\app.js"
PAGE = "C:\\w\\index.html"


def _state():
    state = TrajectoryState({}, goal="Add the storm arcs", message_count=1)
    state.data["written_paths"] = [APP, "C:\\w\\app_test.js"]
    return state


def _edit(state):
    operation = state.before_tool("write_file")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path=APP)


def _tests_pass(state):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, "12 passed in 0.4s", failed=False, blocked=False, mutated=False, verdict="passed", command="pytest")


def _page_check(state, **kw):
    operation = state.before_tool("check_app")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=False, verdict="unknown", path=PAGE)
    state.record_smoke(PAGE, "index.html: smoke test passed", **{"failed": False, "passed": True, **kw})


def _footer(engine, state):
    answer = engine._with_task_notes("Done.", state)
    return [part for part in answer.split("\n\n")[1:] if part.startswith(("Verification", "Task-state"))]


def test_a_page_check_after_the_last_edit_clears_the_pending_state(engine):
    state = _state()
    _edit(state)
    assert state.needs_verification()
    _page_check(state)
    assert not state.needs_verification()
    assert state.data["verification"] == "inspected"
    assert state.assurance() == "exercised"
    lines = _footer(engine, state)
    assert len(lines) == 2 and lines[0].startswith("Verification: exercised")
    assert not any("no fresh automated verification evidence" in line for line in lines)


def test_the_reported_sequence_tests_then_edit_then_page_check(engine):
    state = _state()
    _edit(state)
    _tests_pass(state)
    _edit(state)
    _page_check(state)
    assert state.assurance() == "exercised"
    assert not any("no fresh automated verification evidence" in line for line in _footer(engine, state))


def test_tests_that_passed_after_the_last_edit_stay_tested(engine):
    state = _state()
    _edit(state)
    _tests_pass(state)
    _page_check(state)
    assert state.data["verification"] == "verified" and state.assurance() == "tested"
    assert _footer(engine, state)[0].startswith("Verification: tested")


def test_a_failed_page_check_leaves_the_work_pending(engine):
    state = _state()
    _edit(state)
    state.record_smoke(PAGE, "index.html: smoke test FAILED (console error)", failed=True)
    assert state.needs_verification()
    assert state.assurance() == "failed"


def test_a_page_that_calls_an_unreachable_service_is_not_counted(engine):
    state = _state()
    _edit(state)
    _page_check(state, unverified=True)
    assert state.needs_verification()
    assert any("no fresh automated verification evidence" in line for line in _footer(engine, state))


def test_a_later_edit_makes_the_work_pending_again(engine):
    state = _state()
    _edit(state)
    _page_check(state)
    assert not state.needs_verification()
    _edit(state)
    assert state.needs_verification()
    assert state.assurance() == "unchecked"
    assert any("no fresh automated verification evidence" in line for line in _footer(engine, state))


def test_the_verify_reminder_stops_once_the_page_has_passed():
    state = _state()
    _edit(state)
    _page_check(state)
    assert not state.request_verification()


def test_edit_without_any_check_still_gets_the_note(engine):
    state = _state()
    _edit(state)
    assert any("no fresh automated verification evidence" in line for line in _footer(engine, state))


def test_a_pass_that_a_newer_result_supersedes_no_longer_counts():
    state = _state()
    _edit(state)
    _page_check(state)
    assert not state.needs_verification()
    state.record_smoke(PAGE, "index.html: could not verify", failed=False, unverified=True)
    assert state.needs_verification()
    assert state.assurance() == "unchecked"


def test_another_page_still_passing_keeps_the_state():
    state = _state()
    _edit(state)
    _page_check(state)
    state.record_smoke("C:\\w\\other.html", "other.html: could not verify", failed=False, unverified=True)
    assert not state.needs_verification()


def test_a_superseded_pass_does_not_undo_a_passing_test_run():
    state = _state()
    _edit(state)
    _page_check(state)
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, "12 passed in 0.4s", failed=False, blocked=False, mutated=False, verdict="passed", command="pytest")
    state.record_smoke(PAGE, "index.html: could not verify", failed=False, unverified=True)
    assert state.data["verification"] == "verified"


def _read_back(state):
    operation = state.before_tool("read_text")
    state.after_tool(operation, "contents", failed=False, blocked=False, mutated=False, verdict="unknown", path=APP)


def test_a_passing_test_run_after_a_read_back_makes_the_work_tested():
    state = _state()
    _edit(state)
    _read_back(state)
    assert state.data["verification"] == "inspected"
    _tests_pass(state)
    assert state.data["verification"] == "verified" and state.assurance() == "tested"


def test_a_passing_test_run_after_a_page_check_makes_the_work_tested(engine):
    state = _state()
    _edit(state)
    _page_check(state)
    _tests_pass(state)
    assert state.assurance() == "tested"
    assert _footer(engine, state)[0].startswith("Verification: tested")


def test_a_test_that_passed_before_the_last_edit_does_not_verify_it():
    state = _state()
    _edit(state)
    _tests_pass(state)
    _edit(state)
    _read_back(state)
    assert state.data["verification"] == "inspected" and state.assurance() == "inspected"
