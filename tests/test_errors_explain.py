"""errors.explain_model_error: one plain sentence + one fix per failure kind.

Built from real openai/httpx exception objects where the SDK lets us construct
them (that is what the engine actually sees), and from duck-typed fakes for
the shapes other backends (LiteLLM, urllib) raise.
"""
import socket
import sys
from pathlib import Path

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent8088 import errors  # noqa: E402

URL = "http://localhost:11434/v1"
REQ = httpx.Request("POST", URL + "/chat/completions")


def _status_error(cls, status, message="boom", body=None, headers=None):
    response = httpx.Response(status, request=REQ, headers=headers or {})
    return cls(message, response=response, body=body)


def _connection_refused():
    try:
        raise httpx.ConnectError("[Errno 61] Connection refused")
    except httpx.ConnectError as cause:
        try:
            raise openai.APIConnectionError(request=REQ) from cause
        except openai.APIConnectionError as exc:
            return exc


def test_connection_refused_to_ollama_says_start_ollama_serve():
    f = errors.explain_model_error(_connection_refused(), provider="ollama", base_url=URL)
    assert f.kind == "connection"
    assert f.message == f"Can't reach {URL}."
    assert "ollama serve" in f.fix
    assert f.retryable is False  # refused: retrying shortly does not help
    assert f.render().endswith(errors.DOCTOR_HINT)


def test_connection_refused_elsewhere_points_at_base_url():
    f = errors.explain_model_error(_connection_refused(), provider="vllm",
                                   base_url="http://gpu:8000/v1")
    assert "Can't reach http://gpu:8000/v1" in f.message
    assert "base_url" in f.fix
    assert "ollama" not in f.fix


def test_dns_failure_is_unreachable():
    exc = socket.gaierror(8, "nodename nor servname provided, or not known")
    assert errors.is_unreachable(exc)
    assert errors.explain_model_error(exc, base_url="http://nohost/v1").kind == "connection"


def test_dropped_connection_is_retryable_but_not_unreachable():
    exc = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    f = errors.explain_model_error(exc, base_url=URL)
    assert f.kind == "connection"
    assert f.retryable
    assert not errors.is_unreachable(exc)


def test_timeout_names_the_wait_and_the_setting():
    exc = openai.APITimeoutError(request=REQ)
    f = errors.explain_model_error(exc, base_url=URL, timeout_seconds=120)
    assert f.kind == "timeout"
    assert f.message == f"{URL} didn't answer in 120s."
    assert "timeout_seconds" in f.fix
    assert f.retryable


@pytest.mark.parametrize("status,cls", [(401, openai.AuthenticationError),
                                        (403, openai.PermissionDeniedError)])
def test_auth_errors_name_the_key_variable(status, cls):
    f = errors.explain_model_error(_status_error(cls, status), provider="openrouter",
                                   api_key_env="OPENROUTER_API_KEY")
    assert f.kind == "auth"
    assert "openrouter" in f.message
    assert "OPENROUTER_API_KEY" in f.fix and "agent8088 --setup" in f.fix
    assert not f.retryable


def test_ollama_missing_model_says_pull_it():
    exc = _status_error(openai.NotFoundError, 404,
                        'model "qwen14b-tooluse-v3" not found, try pulling it first')
    f = errors.explain_model_error(exc, provider="ollama", base_url=URL,
                                   model="qwen14b-tooluse-v3")
    assert f.kind == "model_not_found"
    assert "ollama pull qwen14b-tooluse-v3" in f.fix


def test_hosted_missing_model_says_pick_another():
    exc = _status_error(openai.NotFoundError, 404, "The model `gpt-9` does not exist",
                        body={"error": {"code": "model_not_found"}})
    f = errors.explain_model_error(exc, provider="openai", model="gpt-9")
    assert f.kind == "model_not_found"
    assert "/models" in f.fix and "ollama" not in f.fix


def test_404_without_model_text_is_a_wrong_base_url():
    exc = _status_error(openai.NotFoundError, 404, "404 page not found")
    f = errors.explain_model_error(exc, base_url="http://box:8080")
    assert f.kind == "endpoint_not_found"
    assert "/v1" in f.fix


def test_rate_limit_reports_retry_after():
    exc = _status_error(openai.RateLimitError, 429, headers={"retry-after": "7"})
    f = errors.explain_model_error(exc, provider="groq")
    assert f.kind == "rate_limit"
    assert f.retryable and f.retry_after == 7.0
    assert "7s" in f.message
    assert errors.DOCTOR_HINT not in f.render()  # nothing to check: just wait


def test_retry_after_zero_is_a_real_value():
    exc = _status_error(openai.RateLimitError, 429, headers={"retry-after": "0"})
    assert errors.retry_after_seconds(exc) == 0.0


def test_server_error_is_retryable():
    f = errors.explain_model_error(_status_error(openai.InternalServerError, 503, "overloaded"),
                                   base_url=URL)
    assert f.kind == "server_error" and f.retryable
    assert "503" in f.message


@pytest.mark.parametrize("text", [
    "This model's maximum context length is 32768 tokens. However, you requested 40000",
    "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}",
    "prompt is too long: 210000 tokens > 200000 maximum",
    "too many tokens in request",
])
def test_context_overflow_variants(text):
    exc = _status_error(openai.BadRequestError, 400, text)
    assert errors.is_context_overflow(exc)
    f = errors.explain_model_error(exc, provider="vllm")
    assert f.kind == "context_overflow"
    assert "/compact" in f.fix and "provider.vllm.context_window" in f.fix


def test_413_is_context_overflow_without_text():
    exc = _status_error(openai.APIStatusError, 413, "payload")
    assert errors.is_context_overflow(exc)


def test_duck_typed_status_code_is_read():
    class LiteLLMish(Exception):
        status_code = 401

    assert errors.explain_model_error(LiteLLMish("nope"), provider="x").kind == "auth"


def test_unknown_error_keeps_a_short_original_message():
    f = errors.explain_model_error(RuntimeError("something odd\n  happened " + "x" * 500))
    assert f.kind == "unknown"
    assert f.message.startswith("something odd happened")
    assert len(f.message) <= 240


def test_empty_message_uses_the_class_name():
    assert errors.short_message(TimeoutError()) == "TimeoutError"


def test_plain_string_errors_are_classified_without_crashing():
    # The engine's fallback answer is sometimes handed a string, not an exception.
    assert errors.explain_model_error("connection reset").kind == "connection"
    assert errors.explain_model_error("context length exceeded").kind == "context_overflow"


# --- config key checks ---------------------------------------------------

def test_known_config_keys_come_from_the_source():
    known = errors.known_config_keys()
    for key in ("context_window", "timeout_seconds", "default_provider", "memory_engine",
                "fallback_models", "max_turns"):
        assert key in known


def test_unknown_keys_get_did_you_mean():
    warnings = errors.unknown_config_keys(
        {"context_windw": "1", "context_window": "2", "provider.ollama.modle": "x",
         "provider.ollama.model": "y", "tool_timeout.read_text": "5"})
    assert len(warnings) == 2
    assert any("'context_windw'" in w and "context_window" in w for w in warnings)
    assert any("provider.ollama.modle" in w and "'model'" in w for w in warnings)


def test_no_sources_means_no_warnings(tmp_path):
    assert errors.unknown_config_keys({"anything": "1"}, known=frozenset()) == []
