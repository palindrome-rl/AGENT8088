"""run_agent's retry after a MAX_COMPLETION_TOKENS cutoff must match the
failure mode: a reasoning-only overflow needs a 'stop thinking' nudge and a
bigger budget on retry — not the 'split large work across calls' advice and
the same unchanged budget, which just reproduces the same failure.
"""

import pytest


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


@pytest.mark.parametrize("model", ["glm-5.2", "glm-5.3-flash"])
def test_known_active_model_uses_its_reviewed_output_limit(monkeypatch, engine, model):
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", model)
    engine.PROVIDERS["ollama-cloud"] = {"model": model}

    context, completion = engine._active_model_token_limits()

    assert context == 1_048_576
    assert completion == 131_072


def test_agent_call_receives_the_active_models_completion_limit(monkeypatch, engine):
    seen = []
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", "glm-5.2")
    engine.PROVIDERS["ollama-cloud"] = {"model": "glm-5.2"}

    def _fake(messages, tools, max_tokens=None, **kw):
        seen.append(max_tokens)
        return _stop_response("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    result = engine.run_agent(
        [{"role": "user", "content": "build it"}], max_turns=1
    )
    assert result == "done"
    assert seen == [131_072]


def test_provider_token_override_wins_over_known_model(monkeypatch, engine):
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "ollama-cloud")
    monkeypatch.setattr(engine, "MODEL_NAME", "glm-5.3-flash")
    engine.PROVIDERS["ollama-cloud"] = {
        "model": "glm-5.3-flash",
        "context_window": "200000",
        "max_completion_tokens": "24000",
    }

    assert engine._active_model_token_limits() == (200_000, 24_000)


def test_unknown_model_keeps_conservative_defaults(monkeypatch, engine):
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "custom")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "custom")
    monkeypatch.setattr(engine, "MODEL_NAME", "private-model")
    engine.PROVIDERS["custom"] = {"model": "private-model"}

    assert engine._active_model_token_limits() == (
        engine.CONTEXT_WINDOW,
        engine.MAX_COMPLETION_TOKENS,
    )


def test_turn_limit_reports_error_and_latest_tool_result(monkeypatch, engine):
    responses = iter((
        '✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "first"}',
        '✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "second"}',
    ))
    results = iter(("old skill text", "latest failure"))

    def completion(*_args, **_kwargs):
        return _stop_response(next(responses))

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "exec_tool", lambda *_args, **_kwargs: next(results))

    answer = engine.run_agent(
        [{"role": "user", "content": "test"}], max_turns=2,
        system_prompt="", tools_def=[], allowed_tools={"read_text"},
    )

    assert answer.startswith("Error: Agent reached the 2-turn limit")
    assert "latest failure" in answer
    assert "old skill text" not in answer
