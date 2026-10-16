"""A summary that comes back empty because the model spent its allowance reasoning is asked for once more with
thinking turned down the provider's way, so compaction does not fail on every try."""
from types import SimpleNamespace as N

import pytest

TOOL_CALL = "✿FUNCTION✿: execute_shell ✿ARGS✿: {}"


def _response(content, finish="stop"):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason=finish)])


def _msgs(count):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(count)]


@pytest.fixture
def summarizer(engine, monkeypatch):
    state = N(thinking=[], prompts=[], replies=[])

    def fake_completion(client, request, tools, **kwargs):
        state.thinking.append(kwargs.get("thinking"))
        state.prompts.append(request[0]["content"])
        return state.replies[min(len(state.thinking) - 1, len(state.replies) - 1)]

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    monkeypatch.setattr(engine, "_TURN_FILES_TOUCHED", [])
    monkeypatch.setattr(engine, "_VERDICT_LEDGER", {})
    return state


def test_an_empty_first_summary_is_asked_for_again_with_thinking_turned_down(engine, summarizer):
    summarizer.replies = [_response(None, "length"), _response("THE SUMMARY")]
    messages = _msgs(8)
    assert engine.compact_messages(messages, keep=3, completion_client=object())
    assert summarizer.thinking == [False, "length_retry"]
    assert "THE SUMMARY" in messages[0]["content"]


def test_a_summary_that_arrives_is_not_asked_for_twice(engine, summarizer):
    summarizer.replies = [_response("FIRST TRY")]
    assert engine.compact_messages(_msgs(8), keep=3, completion_client=object())
    assert summarizer.thinking == [False]


def test_the_second_request_fences_the_transcript_and_puts_the_instruction_last(engine, summarizer):
    summarizer.replies = [_response(None, "length"), _response("ok summary")]
    assert engine.compact_messages(_msgs(8), keep=3, completion_client=object())
    assert "<transcript>" not in summarizer.prompts[0]
    assert "<transcript>\n" in summarizer.prompts[1]
    assert summarizer.prompts[1].rstrip().endswith("as plain text.")


def test_a_summary_that_is_a_tool_call_is_not_used(engine, summarizer):
    summarizer.replies = [_response(None, "length"), _response(TOOL_CALL)]
    messages = _msgs(8)
    before = list(messages)
    assert not engine.compact_messages(messages, keep=3, completion_client=object())
    assert messages == before


def test_two_empty_summaries_still_leave_the_history_alone(engine, summarizer):
    summarizer.replies = [_response(None, "length")]
    messages = _msgs(8)
    before = list(messages)
    assert not engine.compact_messages(messages, keep=3, completion_client=object())
    assert summarizer.thinking == [False, "length_retry"]
    assert messages == before
