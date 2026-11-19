"""A run ended by the post-check cap or backstop must say what it left undone.

Those paths skip the completion checks on purpose, so they used to return an
answer that read as complete even when a named file was missing.
"""
import json
from types import SimpleNamespace as N

import pytest

from agent8088 import app_check


def _response(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _calc(i):
    return f'✿FUNCTION✿: calculate ✿ARGS✿: {{"expression": "{i}+1"}}'


@pytest.fixture
def eng(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", 3)
    # The pipeline must never start a browser from the cap path.
    monkeypatch.setattr(app_check, "check_target",
                        lambda *a, **k: pytest.fail("the smoke test must not run on the cap path"))
    return engine


def _run(engine, monkeypatch, request, script):
    """script(call_index, messages) -> reply. Each calculate round changes nothing."""
    calls, trace = [], []

    def model(messages, tools, **kwargs):
        calls.append(messages)
        return _response(script(len(calls) - 1, messages))

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "exec_tool", lambda name, arguments, depth=0: "2")
    answer = engine.run_agent([{"role": "user", "content": request}], max_turns=40, trace=trace,
                              dynamic_turns=False)
    return answer, calls, trace


def _told_to_answer(messages):
    return "Checks are complete" in str(messages[-1].get("content"))


def _stalls_then_answers(final):
    """tool, reply (completion check fires), read-only rounds to the cap, then `final`."""
    def script(i, messages):
        if _told_to_answer(messages):
            return final
        return "All done." if i == 1 else _calc(i)
    return script


def test_a_named_file_still_missing_when_the_cap_ends_the_run_is_reported(eng, monkeypatch):
    answer, _, trace = _run(eng, monkeypatch, "Compute it and save the result to out.txt",
                            _stalls_then_answers("I am done."))
    assert answer.startswith("NOT VERIFIED:") and "I am done." in answer.split("\n\n")[1]
    assert "Not completed" in answer and "out.txt" in answer
    assert any(t.get("type") == "unmet_after_cap" and "out.txt" in str(t["unmet"]) for t in trace)


def test_a_satisfied_request_gets_no_note(eng, monkeypatch, tmp_path):
    (tmp_path / "out.txt").write_text("42", encoding="utf-8")
    answer, _, trace = _run(eng, monkeypatch, "Compute it and save the result to out.txt",
                            _stalls_then_answers("I am done."))
    assert "Not completed" not in answer
    assert not any(t.get("type") == "unmet_after_cap" for t in trace)


def test_the_backstop_path_reports_it_too(eng, monkeypatch):
    def script(i, messages):
        if "no more tools will run" in str(messages[-1].get("content")):
            return "Summary of what happened."
        return "All done." if i == 1 else _calc(i)

    answer, _, trace = _run(eng, monkeypatch, "Compute it and save the result to out.txt", script)
    assert any(t.get("type") == "post_check_backstop" for t in trace)
    assert answer.startswith("NOT VERIFIED:") and "Summary of what happened." in answer.split("\n\n")[1]
    assert "Not completed" in answer and "out.txt" in answer


def test_a_reply_that_admits_unfinished_work_is_reported_when_the_cap_ends_the_run(eng, monkeypatch):
    unfinished = "Not done yet - I still need to fix the build."

    def script(i, messages):
        if _told_to_answer(messages):
            return unfinished
        return unfinished if i == 1 else _calc(i)

    answer, _, _ = _run(eng, monkeypatch, "Fix the build", script)
    assert "Not completed: the reply says the work is unfinished" in answer


def test_a_plain_question_is_left_alone(eng, monkeypatch):
    answer, _, _ = _run(eng, monkeypatch, "What is 1+1?", _stalls_then_answers("It is 2."))
    assert "Not completed" not in answer


def test_the_peek_spends_no_budget_and_arms_nothing(eng):
    from agent8088 import completion, trajectory
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    budget = completion.ContinuationBudget(limit=3)
    reasons = eng._record_unmet_checks(budget, state, reply="done", request="Write the notes to notes.md",
                                       depth=0)
    assert reasons and "notes.md" in reasons[0]
    assert budget.spent == 0 and budget.total == 0
    assert not state.data["tests_requested"] and not state.data["verification_requested"]
    assert state.unmet_review().startswith("Not completed: missing or empty files: notes.md")


def test_it_does_nothing_when_the_completion_checks_are_off(eng):
    from agent8088 import completion, trajectory
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    budget = completion.ContinuationBudget(limit=0)
    assert eng._record_unmet_checks(budget, state, reply="done", request="Write notes.md", depth=0) == []
    assert state.unmet_review() == ""


def test_sub_agents_are_not_held_to_the_users_request(eng):
    from agent8088 import completion, trajectory
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    budget = completion.ContinuationBudget(limit=3)
    assert eng._record_unmet_checks(budget, state, reply="done", request="Write notes.md", depth=1) == []


def test_parts_the_reply_covered_are_not_listed_as_open(eng):
    """The cap path skips the checks that mark parts done; the peek marks what the reply covered."""
    from agent8088 import completion, trajectory
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.set_subgoals(["summarize the sales figures", "list the three biggest risks"])
    budget = completion.ContinuationBudget(limit=3)
    eng._record_unmet_checks(budget, state, reply="Sales figures summary: revenue grew 12 percent.",
                             request="1. summarize the sales figures\n2. list the three biggest risks", depth=0)
    assert state.open_subgoals() == ["list the three biggest risks"]
    answer = eng._with_task_notes("Here is the summary.", state)
    assert "Not completed: list the three biggest risks" in answer
    assert "summarize the sales figures" not in answer


def test_an_earlier_unmet_reason_is_kept_and_not_repeated(eng):
    from agent8088 import completion, trajectory
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.note_unmet(["changed work has no passing check"])
    budget = completion.ContinuationBudget(limit=3)
    for _ in range(2):
        eng._record_unmet_checks(budget, state, reply="done", request="Write notes.md", depth=0)
    unmet = state.data["unmet_checks"]
    assert unmet[0] == "changed work has no passing check"
    assert sum("notes.md" in item for item in unmet) == 1
