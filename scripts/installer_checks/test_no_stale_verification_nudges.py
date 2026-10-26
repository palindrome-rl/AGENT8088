"""A question asked after unverified edits must not be answered with a verification reminder about the old edits.

Unresolved state carries over to the next request with its reminder budget reset, so "tell what you did in this turn" got up to
two fresh "changed work has no fresh verification evidence" messages. The model started verifying the old work again, wrote
helper scripts (which re-armed the reminder), and its answer stayed in its thoughts. The reminder now only fires for changes
made in the current request; older unverified work is still disclosed by the final answer's verification note."""
from agent8088.trajectory import TrajectoryState


def _after_a_change_request():
    """State at the end of a request that changed files and never verified them."""
    state = TrajectoryState({}, goal="Fix the file manager", message_count=1)
    operation = state.before_tool("write_file")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path="js/fs.mjs")
    assert state.needs_verification() and state.mutation_count() == 1
    return state


def _next_request(saved, goal="Tell what you did in this turn in brief bullet points", message_count=2):
    return TrajectoryState(saved.data, goal=goal, message_count=message_count)


def test_the_request_that_made_the_change_is_still_reminded():
    state = _after_a_change_request()
    assert state.changed_this_request()
    assert state.request_verification()


def test_a_question_asked_later_is_not_reminded_about_old_work():
    follow_up = _next_request(_after_a_change_request())
    assert follow_up.needs_verification()              # the old work is still unverified ...
    assert not follow_up.changed_this_request()
    assert not follow_up.request_verification()        # ... but this request does not chase it
    assert follow_up.data["verification_requests"] == 0


def test_the_old_work_is_still_disclosed_in_the_answer(engine):
    follow_up = _next_request(_after_a_change_request())
    answer = engine._with_task_notes("Here is what I did.", follow_up)
    assert "Verification note: changes were made, but no fresh automated verification evidence" in answer


def test_a_change_made_in_the_later_request_is_reminded_again():
    follow_up = _next_request(_after_a_change_request(), goal="Also fix the paint dialog")
    operation = follow_up.before_tool("write_file")
    follow_up.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path="js/paint.mjs")
    assert follow_up.changed_this_request()
    assert follow_up.request_verification()


def test_the_cap_per_request_still_holds():
    state = _after_a_change_request()
    assert state.request_verification()
    state.data["verification_requested"] = False
    operation = state.before_tool("write_file")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path="js/b.mjs")
    assert state.request_verification()
    state.data["verification_requested"] = False
    operation = state.before_tool("write_file")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path="js/c.mjs")
    assert not state.request_verification()            # MAX_VERIFICATION_REQUESTS (2) reached


def test_the_same_request_resumed_keeps_its_start():
    state = _after_a_change_request()
    resumed = TrajectoryState(state.data, goal="Fix the file manager", message_count=1)
    assert resumed.changed_this_request() and resumed.data["request_mutation_start"] == 0


def test_an_old_saved_state_without_the_field_behaves_as_before():
    state = _after_a_change_request()
    del state.data["request_mutation_start"]
    reloaded = TrajectoryState(state.data, goal="Fix the file manager", message_count=1)
    assert reloaded.changed_this_request() and reloaded.request_verification()
