"""The user-facing "I could not answer:" line for a recognised failure carries
the explanation and the fix, not the SDK's raw dump (that stays in the trace).
Found live: a 401 showed `Error code: 401 - {'error': {...}}` and a wrong
base_url pasted the provider's HTML home page into the answer."""
from agent8088 import engine as A


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.headers = {}


class _StatusError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status
        self.response = _Resp(status)


def test_auth_failure_has_no_raw_sdk_dump():
    err = _StatusError(401, "Error code: 401 - {'error': {'message': 'Unauthorized'}}")
    answer = A._model_failure_reason(err, "openai", "gpt-x")
    assert answer.startswith("I could not answer: API key rejected by openai (HTTP 401).")
    assert "Error code" not in answer and "{" not in answer


def test_html_404_body_is_not_pasted_into_the_answer():
    err = _StatusError(404, "<!doctype html> <html><head><title>Ollama</title></head></html>")
    answer = A._model_failure_reason(err, "ollama-cloud", "glm-5.3")
    assert "endpoint not found" in answer and "/v1" in answer
    assert "<html" not in answer and "doctype" not in answer


def test_unrecognised_errors_keep_the_raw_detail():
    answer = A._model_failure_reason(RuntimeError("weird backend thing"), "openai", "gpt-x")
    assert "weird backend thing" in answer


def test_a_4xx_with_retryable_words_in_its_body_is_not_retried():
    # Ollama's HTML home page (a 404 for a base_url missing /v1) mentions
    # "connection"-ish words; status codes decide before body text does.
    err = _StatusError(404, "<html>... connection ... timeout ...</html>")
    assert A._retryable_model_error(err) is False
    assert A._retryable_model_error(_StatusError(408, "timeout")) is True
    assert A._retryable_model_error(_StatusError(429, "slow down")) is True
    assert A._retryable_model_error(_StatusError(503, "busy")) is True
