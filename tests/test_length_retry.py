"""run_agent's retry after a MAX_COMPLETION_TOKENS cutoff must match the
failure mode: a reasoning-only overflow needs a 'stop thinking' nudge and a
bigger budget on retry — not the 'split large work across calls' advice and
the same unchanged budget, which just reproduces the same failure.
"""


def _length_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "length",
    })()]})()


def _stop_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "stop",
    })()]})()


def test_reasoning_only_overflow_gets_stop_thinking_nudge(monkeypatch, engine):
    """finish_reason=length with NO visible content (all budget spent on an
    unclosed <think> block) must retry with an instruction to stop reasoning
    and answer immediately — not the generic 'split work into calls' text."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        if len(calls) == 1:
            # Runaway, never-closed <think> block: _strip_reasoning drops it
            # entirely, so `content` at the cutoff is empty.
            return _length_response("<think>reasoning forever with no end")
        return _stop_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    result = engine.run_agent([{"role": "user", "content": "its still off"}],
                              max_turns=5)

    assert len(calls) == 2, "expected exactly one automatic retry"
    retry_nudge = calls[1]["messages"][-1]["content"]
    assert "stop reasoning" in retry_nudge.lower() or "do not think" in retry_nudge.lower()
    assert "split large work" not in retry_nudge.lower()
    assert result == "the actual answer"


def test_large_answer_overflow_keeps_split_work_nudge(monkeypatch, engine):
    """finish_reason=length with SOME visible content (a real answer/tool call
    in progress) should keep today's 'split large work across calls' advice."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        if len(calls) == 1:
            return _length_response("x" * 500)  # a real, oversized answer
        return _stop_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=5)

    retry_nudge = calls[1]["messages"][-1]["content"]
    assert "split large work" in retry_nudge.lower()


def test_retry_after_length_cutoff_gets_a_bigger_budget(monkeypatch, engine):
    """The one automatic retry must ask for more than the default budget, so
    a model that keeps reasoning almost as long still has room for an answer."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append(max_tokens)
        if len(calls) == 1:
            return _length_response("<think>reasoning forever with no end")
        return _stop_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    engine.run_agent([{"role": "user", "content": "its still off"}], max_turns=5)

    assert calls[0] == engine.MAX_COMPLETION_TOKENS
    expected_retry_budget = min(engine.MAX_COMPLETION_TOKENS * 2, engine.CONTEXT_WINDOW)
    assert calls[1] == expected_retry_budget
    assert calls[1] > calls[0]
