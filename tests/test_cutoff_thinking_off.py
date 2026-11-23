"""A cut-off that spent its budget on reasoning (or came back empty) retries
with thinking off from the first cut-off; a growing tool call keeps thinking on
and its larger cap; a prose loop keeps today's behaviour.
"""
import pytest


@pytest.fixture(autouse=True)
def _discard_ladder(engine, monkeypatch):
    """These tests cover the discard-and-retry ladder after a cut-off, which cutoff_keep_reasoning=0 keeps."""
    monkeypatch.setattr(engine, "CUTOFF_KEEP_REASONING", False)


@pytest.fixture(autouse=True)
def _fixed_output_caps(engine, monkeypatch):
    """These tests are about the retry ladder at the old fixed caps (8192 / 4096 / 4096); the context-scaled defaults have their own tests."""
    for flag in ("_INITIAL_CAP_SET", "_MIN_TOKENS_SET", "_THINKING_OFF_SET"):
        monkeypatch.setattr(engine, flag, True)



def _length_response(content, reasoning=""):
    message = type("M", (), {"content": content})()
    if reasoning:
        message.reasoning_content = reasoning
    return type("R", (), {"choices": [type("C", (), {
        "message": message,
        "finish_reason": "length",
    })()]})()


def _stop_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "stop",
    })()]})()


_PARTIAL_CALL = "✿FUNCTION✿: write_file ✿ARGS✿: {\"path\": \"a\", \"content\": \"xxxx"


def _run_two_calls(monkeypatch, engine, first):
    """First call returns `first(max_tokens)`; the second returns a final answer."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append({"max": max_tokens, "thinking": kw.get("thinking"),
                      "nudge": messages[-1]["content"]})
        return first(max_tokens) if len(calls) == 1 else _stop_response("answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    result = engine.run_agent([{"role": "user", "content": "go"}], max_turns=5)
    assert result == "answer"
    return calls


def test_first_empty_cutoff_retries_with_thinking_off(monkeypatch, engine):
    calls = _run_two_calls(monkeypatch, engine, lambda mt: _length_response(""))

    assert calls[1]["thinking"] == "length_retry"
    assert calls[1]["max"] <= engine._output_cap_defaults(engine._active_model_token_limits()[0])[2]
    assert "call one tool or answer right away" in calls[1]["nudge"].lower()


def test_reasoning_heavy_cutoff_retries_with_thinking_off(monkeypatch, engine):
    # Reasoning fills 95% of the cap that was in force, with no visible content.
    calls = _run_two_calls(
        monkeypatch, engine,
        lambda mt: _length_response("", reasoning="r" * int(mt * 4 * 0.95)))

    assert calls[1]["thinking"] == "length_retry"
    assert "call one tool or answer right away" in calls[1]["nudge"].lower()


def test_tool_call_cutoff_keeps_thinking_and_grows_the_cap(monkeypatch, engine):
    calls = _run_two_calls(monkeypatch, engine, lambda mt: _length_response(_PARTIAL_CALL))

    assert calls[1]["thinking"] is None
    assert calls[1]["max"] > calls[0]["max"]


def test_prose_cutoff_keeps_current_behaviour(monkeypatch, engine):
    calls = _run_two_calls(monkeypatch, engine, lambda mt: _length_response("x" * 500))

    assert calls[1]["thinking"] is None
    assert "split large work" in calls[1]["nudge"].lower()
