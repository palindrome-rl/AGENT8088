"""The completion gate: a final text reply that admits unfinished work, or leaves a
requested part or output undone, is sent back to the model (at most
completion_gate_max_continuations times per run) instead of ending the run.
Questions, blockers, plain chat and plan mode are never pushed on."""
import pytest


def _response(content):
    message = type("M", (), {"content": content, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": message,
                                                     "finish_reason": "stop"})()]})()


_CALC = '✿FUNCTION✿: calculate ✿ARGS✿: {"expression": "1+1"}'


def _run(engine, monkeypatch, script, request="compute", *, cap=3, tools=True):
    """script(call_index) -> reply text. Call 0 is a tool call when tools is True."""
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "COMPLETION_GATE_MAX", cap)
    calls = []

    def fake_model(messages, tools_arg, **kwargs):
        index = len(calls)
        calls.append(index)
        if tools and index == 0:
            return _response(_CALC)
        return _response(script(index))

    def fake_exec(name, arguments, depth=0):
        return "2"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    monkeypatch.setattr(engine, "exec_tool", fake_exec)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": request}], max_turns=40,
                              trace=trace, dynamic_turns=False)
    gates = [t for t in trace if t["type"] == "completion_gate"]
    return answer, calls, gates


def test_not_done_after_tool_use_continues_then_ends_when_finished(engine, monkeypatch):
    replies = iter(["Not done yet: the second step is still missing.", "All finished."])
    answer, calls, gates = _run(engine, monkeypatch, lambda i: next(replies))
    assert len(calls) == 3  # tool, unfinished reply, finished reply
    assert [g["kind"] for g in gates] == ["unfinished"]
    assert answer.startswith("All finished.")


def test_continue_message_uses_the_harness_prefix(engine, monkeypatch):
    seen = []
    replies = iter(["Still incomplete.", "Done."])

    def script(i):
        return next(replies)

    monkeypatch.setattr(engine, "_harness_turn", lambda text: seen.append(text) or {
        "role": "user", "content": engine._HARNESS_PREFIX + text})
    _run(engine, monkeypatch, script)
    assert any(text.startswith("Continue; do not stop until the result works.") for text in seen)


def test_a_question_to_the_user_is_not_continued(engine, monkeypatch):
    answer, calls, gates = _run(
        engine, monkeypatch, lambda i: "Not done yet. Which file name should I use?")
    assert len(calls) == 2
    assert gates == []
    assert answer.startswith("Not done yet.")


def test_a_blocker_only_the_user_can_clear_is_not_continued(engine, monkeypatch):
    answer, calls, gates = _run(
        engine, monkeypatch, lambda i: "Not done yet: I need your API key to finish.")
    assert len(calls) == 2
    assert gates == []


def test_plain_chat_without_tool_use_is_unchanged(engine, monkeypatch):
    answer, calls, gates = _run(engine, monkeypatch, lambda i: "Not done yet.", tools=False)
    assert len(calls) == 1
    assert gates == []
    assert answer.startswith("Not done yet.")


def test_two_part_request_is_held_back_once_then_accepted(engine, monkeypatch):
    request = "1. Add a greeting function to the module\n2. Explain how to run the tests"
    replies = iter(["Added the greeting function.",
                    "Still only the greeting function here."])  # part 2 still missing
    answer, calls, gates = _run(engine, monkeypatch, lambda i: next(replies), request)
    assert len(calls) == 3
    assert [g["kind"] for g in gates] == ["parts"]
    assert answer.startswith("Still only the greeting function here.")


def test_two_part_request_covered_by_the_reply_ends_at_once(engine, monkeypatch):
    request = "1. Add a greeting function to the module\n2. Explain how to run the tests"
    answer, calls, gates = _run(
        engine, monkeypatch,
        lambda i: "1) Added the greeting function. 2) Explained how to run the tests.", request)
    assert len(calls) == 2
    assert gates == []


def test_single_part_request_has_no_parts_check(engine, monkeypatch):
    answer, calls, gates = _run(engine, monkeypatch, lambda i: "Added the greeting function.",
                                "Add a greeting function to the module.")
    assert len(calls) == 2
    assert gates == []


def test_a_named_output_that_is_missing_continues(engine, monkeypatch):
    replies = ["Finished the summary.", "Saved it.", "Saved it again."]  # never written
    request = "Create report_for_test_gate.md with the summary"
    answer, calls, gates = _run(engine, monkeypatch,
                                lambda i: replies[min(i - 1, len(replies) - 1)], request)
    assert [g["kind"] for g in gates] == ["output"] * 3  # the cap ends it
    assert len(calls) == 5


def test_cap_ends_a_run_where_the_model_keeps_saying_not_done(engine, monkeypatch):
    answer, calls, gates = _run(engine, monkeypatch,
                                lambda i: "Not done yet: step remains.", cap=3)
    assert len(gates) == 3
    assert len(calls) == 5  # tool + three continuations + the accepted answer
    assert answer.startswith("Not done yet")


def test_zero_disables_the_gate(engine, monkeypatch):
    answer, calls, gates = _run(engine, monkeypatch, lambda i: "Not done yet.", cap=0)
    assert len(calls) == 2
    assert gates == []


def test_plan_mode_flow_is_untouched(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    answer, calls, gates = _run(engine, monkeypatch, lambda i: "Not done yet.")
    assert len(calls) == 2
    assert gates == []


@pytest.mark.parametrize("text", [
    "Not done yet.",
    "The task is still incomplete.",
    "This is not finished.",
    "I haven't completed the second step.",
    "Remaining steps: update the docs.",
    "I still need to write the tests.",
    "TODO: add the migration.",
    "Only part of the request is done.",
    "Partially complete.",
    "I could not complete the export.",
])
def test_unfinished_replies_are_detected(engine, text):
    assert engine._announces_unfinished(text)


@pytest.mark.parametrize("text", [
    "",
    "Done. All three files are written and the tests pass.",
    "Which file should I update?",
    "Not done yet, do you want me to continue?",
    "Not done yet. I need your API key to continue.",
    "I'm blocked: I need permission to write to that folder.",
    "Should I continue with the rest?",
    "The incomplete input handling is fixed.",
    "Let me know if you want more detail.",
    "Todo list is in the README.",
])
def test_finished_questions_and_blockers_are_not_unfinished(engine, text):
    assert not engine._announces_unfinished(text)


def test_request_parts_numbered_and_clause_forms(engine):
    numbered = "1. Add X\n2. Add Y\n3. Add Z"
    assert engine._request_parts(numbered) == ["Add X", "Add Y", "Add Z"]
    assert len(engine._request_parts(
        "\n".join(f"{i}. item {i} here" for i in range(1, 9)))) == 6
    assert len(engine._request_parts("Create app.py. Also write tests for it.")) == 2
    assert len(engine._request_parts("Fix the parser and then update the docs")) == 2


def test_request_parts_ignores_single_part_requests(engine):
    assert engine._request_parts("Fix the parser bug in the tokenizer module.") == []
    assert engine._request_parts("Explain what this does, briefly") == []
    assert engine._request_parts("") == []


def test_requested_output_paths_need_a_save_verb(engine):
    assert engine._requested_output_paths("Create report.md with the summary") == ["report.md"]
    assert engine._requested_output_paths("What does app.py do?") == []
