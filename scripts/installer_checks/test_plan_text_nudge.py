"""A text-only plan gets one tool-submission reminder, never automatic approval."""
import pytest

PLAN = """Here is my plan.

## Goal
A small web app.

## Steps
1. Research the data sources.
2. Write the server.
3. Build the page.
4. Test it.

Approve this plan and I will start.
"""


def test_a_structured_reply_that_asks_for_approval_is_a_plan(engine):
    assert engine._looks_like_a_plan(PLAN * 2)


@pytest.mark.parametrize("text", [
    "",
    "Done.",
    "Which framework do you prefer: Flask or Express?",
    "I looked at the files. " * 40,                                   # long, but no structure
    "- a\n- b\n- c\n" + "x" * 10,                                      # structure, too short
    "1. one\n2. two\n3. three\n" + "The quick brown fox jumps over the lazy dog. " * 12,   # long list, no mention of a plan
])
def test_other_replies_are_not_plans(engine, text):
    assert not engine._looks_like_a_plan(text)


def _completion(replies):
    calls = []

    def completion(*_args, **_kwargs):
        text = replies[min(len(calls), len(replies) - 1)]
        calls.append(True)
        message = type("Message", (), {"content": text})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()
    return completion, calls


def _run(engine, monkeypatch, replies):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    engine.enter_plan_mode()
    completion, calls = _completion(replies)
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    messages = [{"role": "user", "content": "Plan a small web app, then wait for approval."}]
    trace = []
    answer = engine.run_agent(messages, max_turns=6, tools_def=[], allowed_tools={"present_plan"},
                              system_prompt="", memory_capture=False, trace=trace)
    return answer, calls, messages, trace


def test_a_text_plan_gets_one_nudge_and_then_the_answer_stands(engine, monkeypatch):
    answer, calls, messages, trace = _run(engine, monkeypatch, [PLAN * 2])
    nudges = [m for m in messages if m["role"] == "user" and "present_plan" in m["content"]
              and "ordinary text" in m["content"]]
    assert len(nudges) == 1, "the model is sent to present_plan once, not on every reply"
    assert [s["type"] for s in trace].count("plan_text_nudge") == 1
    assert engine.PERMISSION_MODE == "plan-only", "nothing was approved"


def test_a_short_reply_in_plan_mode_is_not_nudged(engine, monkeypatch):
    answer, calls, messages, trace = _run(engine, monkeypatch, ["Which stack do you prefer?"])
    assert not any(s["type"] == "plan_text_nudge" for s in trace)
    assert not any("ordinary text" in str(m.get("content")) for m in messages)


def test_no_nudge_outside_plan_mode(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    completion, calls = _completion([PLAN * 2])
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    messages = [{"role": "user", "content": "Describe a plan."}]
    trace = []
    engine.run_agent(messages, max_turns=4, tools_def=[], allowed_tools=set(), system_prompt="",
                     memory_capture=False, trace=trace)
    assert not any(s["type"] == "plan_text_nudge" for s in trace)
