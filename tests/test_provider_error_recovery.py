"""Provider errors that arrive inside a stream are retried, and the retry changes the request.

A gateway can answer HTTP 200 and then send an error chunk (a 502 "repeating the
same chunk", a 504 idle timeout). Those must be retryable, must not read as an
empty reply, and the retry must run with thinking reduced rather than repeat the
identical request.
"""
from types import SimpleNamespace as N

import pytest


class Failure(RuntimeError):
    def __init__(self, status, text, attr="status_code"):
        super().__init__(text)
        setattr(self, attr, status)


def _response(text="ok"):
    return N(choices=[N(message=N(content=text, tool_calls=None), finish_reason="stop")], usage=None)


@pytest.fixture
def call(engine, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", False)
    monkeypatch.setattr(engine, "_fallback_targets", lambda: [])
    monkeypatch.setattr(engine, "_interruptible_sleep", lambda *a, **k: None)
    seen = []

    def run(outcomes, **kw):
        outcomes = list(outcomes)

        def fake_create(client, messages, tools, **kwargs):
            seen.append(kwargs.get("thinking"))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(engine, "create_completion", fake_create)
        return engine._create_completion_with_fallback(
            [{"role": "user", "content": "hello"}], [], temperature=.1,
            system_prompt="audit", on_token=None, interrupt_check=lambda: False,
            trace=None, turn=1, max_tokens=4096,
            client_override=N(), provider_override="primary", model_override="m", **kw)

    run.seen = seen
    return run


# --- classification --------------------------------------------------------

@pytest.mark.parametrize("attr", ["status_code", "code"])
@pytest.mark.parametrize("status", [502, 504])
def test_streamed_gateway_errors_are_retryable_whichever_attribute_carries_the_code(engine, attr, status):
    """The OpenAI SDK's in-stream error exception carries the code in .code."""
    assert engine._retryable_model_error(Failure(status, "upstream", attr))


def test_client_errors_stay_non_retryable(engine):
    assert not engine._retryable_model_error(Failure(400, "bad request: connection timeout in body"))
    assert not engine._retryable_model_error(Failure(400, "bad request", attr="code"))


def test_stall_detection(engine):
    assert engine._is_provider_stall(Failure(502, "x"))
    assert engine._is_provider_stall(RuntimeError("Provider returned error: idle timeout"))
    assert engine._is_provider_stall(RuntimeError("model is repeating the same chunk"))
    assert not engine._is_provider_stall(Failure(429, "rate limited"))
    assert not engine._is_provider_stall(Failure(401, "denied"))


# --- error chunks ----------------------------------------------------------

def test_error_chunk_raises_with_its_own_code(engine):
    chunk = N(choices=[], error={"message": "Provider idle timeout", "code": "504"})
    with pytest.raises(engine.ProviderStreamError) as caught:
        engine._raise_for_error_chunk(chunk)
    assert caught.value.status_code == 504
    assert engine._retryable_model_error(caught.value)


def test_error_chunk_accepts_object_errors_and_unparseable_codes(engine):
    chunk = N(choices=None, error=N(message="boom", code="upstream_error"))
    with pytest.raises(engine.ProviderStreamError) as caught:
        engine._raise_for_error_chunk(chunk)
    assert caught.value.status_code is None and "boom" in str(caught.value)


def test_error_chunk_read_from_model_extra(engine):
    chunk = N(choices=[], model_extra={"error": {"message": "x", "code": 502}})
    with pytest.raises(engine.ProviderStreamError) as caught:
        engine._raise_for_error_chunk(chunk)
    assert caught.value.status_code == 502


def test_data_chunks_and_empty_keepalives_are_not_errors(engine):
    engine._raise_for_error_chunk(N(choices=[N(delta=N(content="hi"))], error=None))
    engine._raise_for_error_chunk(N(choices=[], error=None))
    engine._raise_for_error_chunk(N(choices=[]))


def test_litellm_stream_error_chunk_is_not_indexed_as_a_choice(engine, monkeypatch):
    """The LiteLLM loop used chunk.choices[0] unguarded: an error chunk was an IndexError."""
    import sys
    fake = N(completion=lambda **kw: iter([N(choices=[], error={"message": "idle timeout", "code": 504})]))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    with pytest.raises(Exception) as caught:
        engine._create_completion(
            {"api_mode": "litellm"}, [{"role": "user", "content": "hi"}], [], model_name="m",
            provider_name="litellm", on_token=lambda *a: None)
    assert isinstance(caught.value, engine.ProviderStreamError) and caught.value.status_code == 504


# --- retry behaviour -------------------------------------------------------

def test_repetition_stop_is_retried_once_with_thinking_reduced(call):
    result = call([Failure(502, "model is repeating the same chunk"), _response("fine")])
    assert result.choices[0].message.content == "fine"
    assert call.seen == [None, "length_retry"]


def test_identical_stall_then_success_reduces_thinking_for_the_retry(call):
    call([Failure(504, "Provider idle timeout"), _response()])
    assert call.seen == [None, "length_retry"]


def test_non_stall_retryable_error_keeps_the_request_unchanged(call):
    call([Failure(429, "rate limited"), _response()])
    assert call.seen == [None, None]


def test_caller_chosen_thinking_is_not_overridden(call):
    call([Failure(502, "x"), _response()], thinking="length_retry")
    assert call.seen == ["length_retry", "length_retry"]


def test_non_retryable_client_error_still_raises_immediately(call):
    with pytest.raises(Failure):
        call([Failure(400, "bad request"), _response()])
    assert call.seen == [None]


def test_default_backoff_window_covers_about_half_a_minute(engine, monkeypatch):
    monkeypatch.setattr(engine, "API_RETRY_JITTER_RATIO", 0.0)
    assert engine.API_MAX_RETRIES == 5
    total = sum(engine._retry_delay(n) for n in range(1, engine.API_MAX_RETRIES + 1))
    assert 30 <= total <= 32


# --- cut-off ladder ---------------------------------------------------------

def _length(content):
    return N(choices=[N(message=N(content=content), finish_reason="length")])


def _stop(content):
    return N(choices=[N(message=N(content=content), finish_reason="stop")])


def test_first_retry_after_an_empty_cutoff_runs_with_thinking_off(monkeypatch, engine):
    seen = []

    def fake(messages, tools, max_tokens=None, **kw):
        seen.append(kw.get("thinking"))
        return _length("<think>reasoning forever") if len(seen) == 1 else _stop("answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
    assert engine.run_agent([{"role": "user", "content": "go"}], max_turns=5) == "answer"
    assert seen == [None, "length_retry"]


def test_first_retry_after_a_cut_off_tool_call_keeps_thinking_on(monkeypatch, engine):
    seen = []
    partial = '✿FUNCTION✿: write_file ✿ARGS✿: {"path": "a", "content": "xx'

    def fake(messages, tools, max_tokens=None, **kw):
        seen.append(kw.get("thinking"))
        return _length(partial) if len(seen) == 1 else _stop("answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
    engine.run_agent([{"role": "user", "content": "go"}], max_turns=5)
    assert seen == [None, None]
