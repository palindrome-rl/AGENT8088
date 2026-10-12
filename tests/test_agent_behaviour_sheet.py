"""Harness-side fixes for the small-model QA sheet (gemma-4-26b, Qwen 3.8 27B,
Gemma 4 31B): what the model is told, and when the harness stops it."""
import httpx
import openai

from agent8088 import errors


# --- 401/403: say the key is bad and where to fix it, never print it ----------

def _auth_error(status=401, body=None):
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    body = body or {"message": "User not found.", "code": status}
    response = httpx.Response(status, request=request, json={"error": body})
    cls = openai.AuthenticationError if status == 401 else openai.PermissionDeniedError
    return cls(f"Error code: {status} - {{'error': {body}}}", response=response, body=body)


def test_401_names_the_provider_the_bad_key_and_the_fix():
    friendly = errors.explain_model_error(_auth_error(), provider="openrouter",
                                          api_key_env="OPENROUTER_API_KEY",
                                          env_file="/home/u/.agent8088/.env")
    assert friendly.kind == "auth"
    assert "openrouter" in friendly.message and "invalid or revoked" in friendly.message
    assert "OPENROUTER_API_KEY in /home/u/.agent8088/.env" in friendly.fix
    assert "/model setup" in friendly.fix


def test_403_also_reads_as_a_key_problem():
    friendly = errors.explain_model_error(_auth_error(403), provider="openrouter")
    assert friendly.kind == "auth" and "revoked" in friendly.message
    assert "your .env file" in friendly.fix


def test_the_chat_answer_is_clear_and_never_echoes_the_key(engine, monkeypatch):
    monkeypatch.setitem(engine.PROVIDERS, "openrouter", {"api_key_env": "OPENROUTER_API_KEY"})
    answer = engine._model_failure_reason(_auth_error(), "openrouter", "google/gemma-4-26b-a4b")
    assert answer.startswith("I could not answer: API key rejected by openrouter (HTTP 401). "
                             "The key is invalid or revoked.")
    assert f"OPENROUTER_API_KEY in {engine.ENV_FILE_PATH}" in answer
    assert "Error code" not in answer and "{" not in answer

    class ShortAuthError(Exception):
        status_code = 401

    leaky = engine._model_failure_reason(
        ShortAuthError("Incorrect API key provided: sk-or-v1-abcd****wxyz"), "openrouter", "m")
    assert "sk-or" not in leaky and "wxyz" not in leaky
