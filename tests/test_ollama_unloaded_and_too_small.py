"""A local Ollama model that isn't loaded yet, and a context too small for the prompt.

An unloaded model has no served context in /api/ps, so the window was sized
to the model's maximum and Ollama silently cut the prompt at its real, much
smaller context. And when agent8088's own instructions did not fit, the error
said to /compact or /reset, which cannot help on a first message.
"""

import pytest

from agent8088 import errors, providers


class _R:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def _fake_ollama(monkeypatch, *, loads=True, served=4096):
    """An Ollama where the model is unloaded until /api/generate loads it."""
    import httpx
    state = {"loaded": False, "generate_calls": []}

    def get(url, **k):
        assert url.endswith("/api/ps")
        models = [{"name": "bonsai-2:9b", "context_length": served}] if state["loaded"] else []
        return _R(200, {"models": models})

    def post(url, json=None, **k):
        if url.endswith("/api/show"):
            return _R(200, {"parameters": "temperature 1\ntop_k 20"})
        assert url.endswith("/api/generate")
        state["generate_calls"].append(json)
        if not loads:
            return _R(500, {"error": "model requires more system memory"})
        state["loaded"] = True
        return _R(200, {"done": True, "done_reason": "load"})

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.delenv("OLLAMA_CONTEXT_LENGTH", raising=False)
    return state


def test_an_unloaded_model_is_loaded_to_read_its_served_context(monkeypatch):
    state = _fake_ollama(monkeypatch)
    assert providers.ollama_served_context("http://h:11434/v1", "bonsai-2:9b", load=True) == (
        4096, "ollama ps")
    assert state["generate_calls"] == [{"model": "bonsai-2:9b"}], "loads only, with no prompt"


def test_without_load_an_unloaded_model_stays_unknown(monkeypatch):
    state = _fake_ollama(monkeypatch)
    assert providers.ollama_served_context("http://h:11434/v1", "bonsai-2:9b") == (None, "unknown")
    assert state["generate_calls"] == []


def test_a_model_that_wont_load_falls_back_to_the_environment(monkeypatch):
    _fake_ollama(monkeypatch, loads=False)
    monkeypatch.setenv("OLLAMA_CONTEXT_LENGTH", "8192")
    assert providers.ollama_served_context("http://h:11434/v1", "bonsai-2:9b", load=True) == (
        8192, "OLLAMA_CONTEXT_LENGTH")


def test_the_engine_probe_asks_for_the_load(engine, monkeypatch):
    seen = {}

    def served(*a, **k):
        seen.update(k)
        return 16384, "ollama ps"

    monkeypatch.setattr(providers, "ollama_served_context", served)

    class _Client:
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"

    monkeypatch.setattr(engine, "client", _Client())
    assert engine._ollama_served_cap("ollama", "bonsai-2:9b", 262144) == 16384
    assert seen.get("load") is True


# --- a context too small for the instructions --------------------------------------------------

SYSTEM = "s" * 44_000  # about 11k tokens
MESSAGES = [{"role": "user", "content": "create an svg of a pelican on a bicycle"}]


def _window(engine, monkeypatch, tokens):
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *_a: (tokens, tokens))
    monkeypatch.setitem(engine.PROVIDERS, "ollama", {"native_tools": False})


def test_instructions_that_dont_fit_raise_context_too_small(engine, monkeypatch):
    _window(engine, monkeypatch, 4096)
    with pytest.raises(errors.ContextTooSmall) as caught:
        engine._completion_token_budget("ollama", "bonsai-2:9b", MESSAGES, [], SYSTEM, 4096)
    assert caught.value.context == 4096 and caught.value.needed > 11_000
    assert not errors.is_context_overflow(caught.value), "no compaction retry for this"


def test_a_long_conversation_is_still_an_ordinary_overflow(engine, monkeypatch):
    _window(engine, monkeypatch, 16384)
    long = [{"role": "user", "content": "w " * 40_000}]
    with pytest.raises(ValueError) as caught:
        engine._completion_token_budget("ollama", "bonsai-2:9b", long, [], SYSTEM, 16384)
    assert not isinstance(caught.value, errors.ContextTooSmall)
    assert errors.is_context_overflow(caught.value)


def test_the_message_for_ollama_says_how_to_raise_its_context():
    exc = errors.ContextTooSmall("ollama", "bonsai-2:9b", 11_600, 4096)
    friendly = errors.explain_model_error(exc, provider="ollama",
                                          base_url="http://localhost:11434/v1")
    assert friendly.kind == "context_too_small"
    assert "OLLAMA_CONTEXT_LENGTH" in friendly.fix
    assert "provider.ollama.context_window" in friendly.fix
    assert "/compact" not in friendly.fix and "/reset" not in friendly.fix
    assert "4,096-token context" in str(exc)


def test_the_message_for_other_providers_says_to_use_a_larger_context():
    exc = errors.ContextTooSmall("custom", "tiny", 11_600, 8192)
    friendly = errors.explain_model_error(exc, provider="custom", base_url="http://h:8000/v1")
    assert friendly.kind == "context_too_small"
    assert "larger context" in friendly.fix and "/compact" not in friendly.fix
