"""_create_completion_with_fallback must retry the primary provider on
transient errors before falling through to fallback_models, honoring
Retry-After and skipping retries on non-retryable errors."""

import pytest


class _RetryableError(Exception):
    status_code = 429


class _AuthError(Exception):
    status_code = 401


def _response(text="ok"):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": text})(),
        "finish_reason": "stop",
    })()]})()


def _patch_common(monkeypatch, engine):
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    monkeypatch.setattr(engine.random, "random", lambda: 0.5)
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 3)
    monkeypatch.setattr(engine, "API_RETRY_INITIAL_DELAY_MS", 500)
    monkeypatch.setattr(engine, "API_RETRY_MAX_DELAY_MS", 10000)
    monkeypatch.setattr(engine, "API_RETRY_JITTER_RATIO", 0.1)


def test_retries_primary_before_failover(monkeypatch, engine):
    calls = []

    def _fake(client, messages, tools, **kw):
        calls.append(kw.get("telemetry_attempt"))
        if len(calls) < 3:
            raise _RetryableError("rate limited")
        return _response()

    _patch_common(monkeypatch, engine)
    monkeypatch.setattr(engine, "create_completion", _fake)

    result = engine._create_completion_with_fallback(
        [{"role": "user", "content": "hi"}], [], temperature=0.1,
        system_prompt="", on_token=None, interrupt_check=lambda: None,
        trace=None, turn=1,
    )

    assert len(calls) == 3
    assert all(c == "primary" for c in calls)
    assert result.choices[0].message.content == "ok"


def test_non_retryable_error_raises_immediately(monkeypatch, engine):
    calls = []

    def _fake(client, messages, tools, **kw):
        calls.append(1)
        raise _AuthError("bad key")

    _patch_common(monkeypatch, engine)
    monkeypatch.setattr(engine, "create_completion", _fake)

    with pytest.raises(_AuthError):
        engine._create_completion_with_fallback(
            [{"role": "user", "content": "hi"}], [], temperature=0.1,
            system_prompt="", on_token=None, interrupt_check=lambda: None,
            trace=None, turn=1,
        )
    assert len(calls) == 1, "no retries for a non-retryable error"


def test_exhausted_retries_fall_through_to_fallback(monkeypatch, engine):
    calls = []

    def _fake(client, messages, tools, **kw):
        calls.append(kw.get("telemetry_attempt"))
        if kw.get("telemetry_attempt") == "primary":
            raise _RetryableError("rate limited")
        return _response("from fallback")

    _patch_common(monkeypatch, engine)
    monkeypatch.setattr(engine, "create_completion", _fake)
    monkeypatch.setattr(engine, "_fallback_targets",
                         lambda: [("openrouter", "some-model")])
    monkeypatch.setattr(engine, "get_client", lambda name: (object(), None))

    result = engine._create_completion_with_fallback(
        [{"role": "user", "content": "hi"}], [], temperature=0.1,
        system_prompt="", on_token=None, interrupt_check=lambda: None,
        trace=None, turn=1,
    )

    assert calls.count("primary") == engine.API_MAX_RETRIES + 1
    assert calls[-1] == "fallback"
    assert result.choices[0].message.content == "from fallback"


def test_api_max_retries_zero_means_immediate_failover(monkeypatch, engine):
    calls = []

    def _fake(client, messages, tools, **kw):
        calls.append(kw.get("telemetry_attempt"))
        if kw.get("telemetry_attempt") == "primary":
            raise _RetryableError("rate limited")
        return _response("from fallback")

    _patch_common(monkeypatch, engine)
    monkeypatch.setattr(engine, "API_MAX_RETRIES", 0)
    monkeypatch.setattr(engine, "create_completion", _fake)
    monkeypatch.setattr(engine, "_fallback_targets",
                         lambda: [("openrouter", "some-model")])
    monkeypatch.setattr(engine, "get_client", lambda name: (object(), None))

    engine._create_completion_with_fallback(
        [{"role": "user", "content": "hi"}], [], temperature=0.1,
        system_prompt="", on_token=None, interrupt_check=lambda: None,
        trace=None, turn=1,
    )

    assert calls.count("primary") == 1


def test_retry_after_over_max_delay_skips_remaining_retries(monkeypatch, engine):
    calls = []

    class _RateLimitedResp:
        headers = {"retry-after": "9999"}  # seconds -> way over max_delay_ms

    def _fake(client, messages, tools, **kw):
        calls.append(kw.get("telemetry_attempt"))
        if kw.get("telemetry_attempt") == "primary":
            err = _RetryableError("rate limited")
            err.response = _RateLimitedResp()
            raise err
        return _response("from fallback")

    _patch_common(monkeypatch, engine)
    monkeypatch.setattr(engine, "create_completion", _fake)
    monkeypatch.setattr(engine, "_fallback_targets",
                         lambda: [("openrouter", "some-model")])
    monkeypatch.setattr(engine, "get_client", lambda name: (object(), None))

    engine._create_completion_with_fallback(
        [{"role": "user", "content": "hi"}], [], temperature=0.1,
        system_prompt="", on_token=None, interrupt_check=lambda: None,
        trace=None, turn=1,
    )

    assert calls.count("primary") == 1, "one huge Retry-After should skip remaining retries"
    assert calls[-1] == "fallback"


def test_extract_retry_after_parses_seconds(engine):
    class _Resp:
        headers = {"retry-after": "5"}

    err = Exception("rate limited")
    err.response = _Resp()
    assert engine._extract_retry_after(err) == 5000


def test_retry_delay_uses_retry_after_when_within_cap(engine):
    assert engine._retry_delay(1, retry_after_ms=2000) == 2.0


def test_retry_delay_exponential_backoff_respects_cap(monkeypatch, engine):
    monkeypatch.setattr(engine.random, "random", lambda: 0.5)  # no jitter noise
    monkeypatch.setattr(engine, "API_RETRY_INITIAL_DELAY_MS", 500)
    monkeypatch.setattr(engine, "API_RETRY_MAX_DELAY_MS", 10000)
    monkeypatch.setattr(engine, "API_RETRY_JITTER_RATIO", 0.0)
    assert engine._retry_delay(1) == pytest.approx(0.5)
    assert engine._retry_delay(2) == pytest.approx(1.0)
    assert engine._retry_delay(50) == pytest.approx(10.0)  # capped
