"""The prompt-size estimate counts the tool schemas only when they are sent.

Without native tool calling the tool catalogue is written into the system
prompt and no JSON schemas go with the request. Counting both measured the
tool list twice, so a 16k local model was refused before the first request
although the real prompt fitted.
"""

import pytest


SYSTEM = "s" * 44_000          # about 11k tokens, a text-tool system prompt
TOOLS = [{"type": "function", "function": {"name": f"tool_{i}", "description": "d" * 540,
                                           "parameters": {"type": "object", "properties": {}}}}
         for i in range(60)]   # about 9k tokens of schemas
MESSAGES = [{"role": "user", "content": "create an svg of a pelican on a bicycle"}]


@pytest.fixture
def limits(engine, monkeypatch):
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *_a: (16_384, 16_384))
    return engine


def _native(engine, monkeypatch, on):
    monkeypatch.setitem(engine.PROVIDERS, "p", {"native_tools": on})


def test_text_tools_do_not_count_the_unsent_schemas(limits, monkeypatch):
    _native(limits, monkeypatch, False)
    budget = limits._completion_token_budget("p", "m", MESSAGES, TOOLS, SYSTEM, 16_384)
    assert budget > 4_000


def test_native_tools_still_count_the_schemas(limits, monkeypatch):
    _native(limits, monkeypatch, True)
    # The instructions plus the sent schemas alone don't fit 16k.
    from agent8088.errors import ContextTooSmall
    with pytest.raises(ContextTooSmall):
        limits._completion_token_budget("p", "m", MESSAGES, TOOLS, SYSTEM, 16_384)


def test_the_fallback_budget_counts_schemas_only_when_sent(limits, monkeypatch):
    _native(limits, monkeypatch, False)
    text_budget = limits._fallback_max_tokens(MESSAGES, SYSTEM, TOOLS, "p", "m")
    _native(limits, monkeypatch, True)
    native_budget = limits._fallback_max_tokens(MESSAGES, SYSTEM, TOOLS, "p", "m")
    assert text_budget > 4_000
    assert native_budget < text_budget


def test_sent_schema_chars(engine, monkeypatch):
    monkeypatch.setitem(engine.PROVIDERS, "p", {"native_tools": False})
    assert engine._sent_tool_schema_chars(TOOLS, "p") == 0
    monkeypatch.setitem(engine.PROVIDERS, "p", {"native_tools": True})
    assert engine._sent_tool_schema_chars(TOOLS, "p") > 30_000
    assert engine._sent_tool_schema_chars([], "p") == 0
