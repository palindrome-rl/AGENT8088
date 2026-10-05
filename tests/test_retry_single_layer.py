"""_create_completion_with_fallback is the only retry layer.

The SDK clients used to keep their own 2 retries underneath the engine's
API_MAX_RETRIES, so a dead endpoint cost 12 attempts and minutes of a frozen
spinner. These pin the single layer: SDK retries off, fail fast on a refused
connection, an interruptible backoff that says what it is doing, and a final
error that is still the primary's.
"""
import threading
import time

import httpx
import openai
import pytest

REQ = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")


def _response(text="ok"):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": text, "tool_calls": None})(),
        "finish_reason": "stop",
    })()]})()


class _RateLimited(Exception):
    status_code = 429


def _refused():
    try:
        raise httpx.ConnectError("[Errno 61] Connection refused")
    except httpx.ConnectError as cause:
        try:
            raise openai.APIConnectionError(request=REQ) from cause
        except openai.APIConnectionError as exc:
            return exc


def _call(engine, **kw):
    return engine._create_completion_with_fallback(
        [{"role": "user", "content": "hi"}], [], temperature=0.1,
        system_prompt="", on_token=kw.pop("on_token", None),
        interrupt_check=kw.pop("interrupt_check", lambda: False),
        trace=None, turn=1, **kw)


@pytest.fixture
def quick(monkeypatch, engine):
    sleeps = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(engine.random, "random", lambda: 0.5)
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 3)
    monkeypatch.setattr(engine, "API_RETRY_INITIAL_DELAY_MS", 500)
    monkeypatch.setattr(engine, "API_RETRY_MAX_DELAY_MS", 10000)
    monkeypatch.setattr(engine, "API_RETRY_JITTER_RATIO", 0.0)
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [])
    return sleeps


def test_runtime_clients_have_sdk_retries_off(engine):
    client, _ = engine.get_client()
    assert client.max_retries == 0
    engine.PROVIDERS["box"] = {"base_url": "http://192.0.2.1:9/v1", "model": "m"}
    client, _ = engine.get_client("box")
    assert client.max_retries == 0
    assert client.timeout == engine.TIMEOUT_SECONDS


def test_gemma_client_gets_the_timeout_too(engine, monkeypatch):
    monkeypatch.setenv("USE_GEMMA4", "1")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "")
    monkeypatch.delenv("AGENT8088_PROVIDER", raising=False)
    client, _ = engine.get_client()
    assert client.timeout == engine.TIMEOUT_SECONDS
    assert client.max_retries == 0


def test_connection_refused_fails_fast(engine, monkeypatch, quick):
    calls = []

    def fake(*a, **kw):
        calls.append(1)
        raise _refused()

    monkeypatch.setattr(engine, "create_completion", fake)
    with pytest.raises(openai.APIConnectionError):
        _call(engine)
    assert len(calls) == 1 and quick == []


def test_retry_after_zero_means_go_now(engine):
    assert engine._retry_delay(1, retry_after_ms=0) == 0.0


def test_fractional_retry_after_is_parsed(engine):
    err = _RateLimited("slow down")
    err.response = type("Resp", (), {"headers": {"retry-after": "1.5"}})()
    assert engine._extract_retry_after(err) == 1500


def test_each_retry_is_announced(engine, monkeypatch, quick):
    attempts = []

    def fake(*a, **kw):
        attempts.append(1)
        if len(attempts) < 3:
            raise _RateLimited("rate limited")
        return _response()

    notes = []
    monkeypatch.setattr(engine, "create_completion", fake)
    assert _call(engine, on_retry=notes.append).choices[0].message.content == "ok"
    assert notes == [
        "Model call failed (429); retrying in 0s -- attempt 2/4",
        "Model call failed (429); retrying in 1s -- attempt 3/4",
    ]
    assert sum(quick) == pytest.approx(1.5)  # 0.5s then 1.0s, in interruptible slices
    assert max(quick) <= 0.1 + 1e-9


def test_backoff_sleep_stops_on_interrupt(engine, monkeypatch):
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 2)
    monkeypatch.setattr(engine, "API_RETRY_INITIAL_DELAY_MS", 5000)
    monkeypatch.setattr(engine, "API_RETRY_MAX_DELAY_MS", 10000)
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [])
    monkeypatch.setattr(engine, "create_completion",
                        lambda *a, **kw: (_ for _ in ()).throw(_RateLimited("rate limited")))
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    with pytest.raises(engine.AgentInterrupted):
        _call(engine, interrupt_check=stop.is_set)
    assert time.monotonic() - started < 2, "the 5s backoff ignored Stop"


def test_primary_error_survives_a_failing_fallback(engine, monkeypatch, quick):
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 0)
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [("other", "m2")])
    monkeypatch.setattr(engine, "get_client", lambda name: (object(), None))

    class _FallbackBroke(Exception):
        status_code = 500

    def fake(*a, **kw):
        if kw["telemetry_attempt"] == "primary":
            raise _RateLimited("primary rate limited")
        raise _FallbackBroke("fallback down")

    monkeypatch.setattr(engine, "create_completion", fake)
    with pytest.raises(_RateLimited) as caught:
        _call(engine)
    assert any("fallback down" in note for note in getattr(caught.value, "__notes__", []))


def test_missing_model_tries_configured_fallbacks(engine, monkeypatch, quick):
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [("other", "m2")])
    monkeypatch.setattr(engine, "get_client", lambda name: (object(), None))
    seen = []

    def fake(*a, **kw):
        seen.append(kw["telemetry_attempt"])
        if kw["telemetry_attempt"] == "primary":
            raise openai.NotFoundError(
                'model "gone" not found, try pulling it first',
                response=httpx.Response(404, request=REQ), body=None)
        return _response("from fallback")

    monkeypatch.setattr(engine, "create_completion", fake)
    assert _call(engine).choices[0].message.content == "from fallback"
    assert seen == ["primary", "fallback"]


def test_bad_key_still_raises_immediately_even_with_fallbacks(engine, monkeypatch, quick):
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [("other", "m2")])
    calls = []

    def fake(*a, **kw):
        calls.append(kw["telemetry_attempt"])
        raise openai.AuthenticationError("bad key", response=httpx.Response(401, request=REQ),
                                         body=None)

    monkeypatch.setattr(engine, "create_completion", fake)
    with pytest.raises(openai.AuthenticationError):
        _call(engine)
    assert calls == ["primary"]


def test_a_stream_that_drops_mid_reply_is_retried_once(engine, monkeypatch, quick):
    calls = []

    def fake(client, messages, tools, on_token=None, **kw):
        calls.append(1)
        on_token("content", "partial ")
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("peer closed connection")
        on_token("content", "whole answer")
        return _response("whole answer")

    resets, notes, tokens = [], [], []
    monkeypatch.setattr(engine, "create_completion", fake)
    result = _call(engine, on_token=lambda kind, delta: tokens.append(delta),
                   on_retry=notes.append, on_stream_reset=lambda: resets.append(1))
    assert result.choices[0].message.content == "whole answer"
    assert len(calls) == 2 and resets == [1]
    assert engine.STREAM_RESET_NOTE in notes[0]


def test_a_second_mid_stream_drop_is_not_retried(engine, monkeypatch, quick):
    calls = []

    def fake(client, messages, tools, on_token=None, **kw):
        calls.append(1)
        on_token("content", "partial ")
        raise httpx.RemoteProtocolError("peer closed connection")

    monkeypatch.setattr(engine, "create_completion", fake)
    with pytest.raises(httpx.RemoteProtocolError):
        _call(engine, on_token=lambda kind, delta: None)
    assert len(calls) == 2
