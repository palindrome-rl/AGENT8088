"""The extraction call's own output budget, and what happens when it runs out.

Live, on ollama-cloud:glm-5.3, a clean store and a 200-word request ending
"please remember for next time that i work at a coffee roastery" stored NOTHING
and reported "nothing new to remember". The same text extracted nine facts when
called without the harness's cap.

The cap was `max_tokens=800`, and the extractor is the chat model — a reasoning
one here. It spent the budget thinking, and what came back was:

    finish_reason: length   completion_tokens: 800
    {"memories": [{"text

Twenty characters, truncated mid-key. `parse_response` returns [] for anything
not strictly parseable, `_capture` maps [] to 0, and 0 prints as "nothing new
to remember" — indistinguishable from a turn that genuinely taught nothing.

So the budget has to fit a reasoning preamble, and running out has to be said
out loud rather than reported as an empty turn.
"""

import logging


def test_the_extraction_budget_leaves_room_for_a_reasoning_model(engine):
    """800 tokens is under one reasoning preamble, never mind the JSON."""
    assert engine.MEMORY_EXTRACT_MAX_TOKENS >= 2000


def test_the_budget_is_configurable(engine):
    """A slower or chattier extractor needs a different number, and nobody
    should have to edit engine.py to give it one."""
    assert "memory_extract_max_tokens" in engine.LIMIT_SPECS
    name, caster, _help = engine.LIMIT_SPECS["memory_extract_max_tokens"]
    assert name == "MEMORY_EXTRACT_MAX_TOKENS" and caster is int


def test_the_extraction_call_asks_for_the_whole_budget(engine, monkeypatch):
    seen = {}

    def fake_completion(client, messages, tools, **kw):
        seen.update(kw)
        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": '{"memories": []}'})(),
                                      "finish_reason": "stop"})()]
            usage = None
        return R()

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    engine._memory_extract_completion("prompt")

    assert seen["max_tokens"] == engine.MEMORY_EXTRACT_MAX_TOKENS


def test_a_truncated_extraction_is_reported_not_swallowed(engine, monkeypatch, caplog):
    """"It ran and found nothing" and "it was cut off mid-sentence" are
    different, and only one of them is a problem. Silence made this invisible
    for an entire session's worth of turns.
    """
    def fake_completion(client, messages, tools, **kw):
        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": '{"memories": [{"text'})(),
                                      "finish_reason": "length"})()]
            usage = None
        return R()

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    with caplog.at_level(logging.WARNING):
        text, usage = engine._memory_extract_completion("prompt")

    assert usage.get("truncated") is True
    assert any("truncat" in record.message.lower() or "cut off" in record.message.lower()
               for record in caplog.records), "a cut-off extraction said nothing at all"


def test_an_ordinary_extraction_is_not_flagged(engine, monkeypatch):
    """The flag must mean something — a normal reply must not carry it."""
    def fake_completion(client, messages, tools, **kw):
        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": '{"memories": []}'})(),
                                      "finish_reason": "stop"})()]
            usage = None
        return R()

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    _text, usage = engine._memory_extract_completion("prompt")

    assert not usage.get("truncated")
