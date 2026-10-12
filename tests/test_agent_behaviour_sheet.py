"""Harness-side fixes for the small-model QA sheet (gemma-4-26b, Qwen 3.8 27B,
Gemma 4 31B): what the model is told, and when the harness stops it."""
import json

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


# --- git commit/push: a pointer to the git tools, never a bare "blocked" -------

def _model(engine, monkeypatch, replies):
    calls = []

    def fake_model(messages, tools, **kwargs):
        calls.append([m.get("content") for m in messages])
        content = replies[min(len(calls) - 1, len(replies) - 1)]
        message = type("M", (), {"content": content, "tool_calls": None})()
        return type("R", (), {"choices": [type("C", (), {"message": message,
                                                         "finish_reason": "stop"})()]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    return calls


def test_shell_git_push_is_still_refused_but_points_at_the_git_tools(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    result = engine.run_tool("execute_shell",
                             {"command": "git add . && git commit -m 'wip' && git push"})
    assert result.startswith("Error: This shell operation is forbidden by Agent8088's safety policy.")
    action = json.loads(result.split("\n", 1)[1])["suggested_action"]
    assert "git_commit" in action and "git_push" in action and "approval prompt" in action
    assert (engine.run_tool("execute_shell", {"command": "rm -rf /"})
            == "Error: This shell operation is forbidden by Agent8088's safety policy.")


def test_git_push_still_needs_the_users_approval(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    assert engine.run_tool("git_push", {}).startswith("ESCALATION_REQUEST\x1f")


def test_a_sandbox_refused_git_write_names_the_git_tools(engine, tmp_path):
    denied = ("fatal: Unable to create 'C:/p/.git/index.lock': Permission denied\n"
              "Command exited with status 128.")
    note = engine._explain_sandbox_denial(denied, tmp_path, "git add -A && git commit -m wip")
    assert "git_commit" in note and "ask the user" not in note
    assert "git_commit" not in engine._explain_sandbox_denial(denied, tmp_path, "pip install x")


def test_a_declined_push_does_not_claim_readonly_mode(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    calls = _model(engine, monkeypatch, ["✿FUNCTION✿: git_push ✿ARGS✿: {}",
                                         "Committed, but the push was declined."])
    monkeypatch.setattr(engine, "exec_tool", lambda name, arguments, depth=0:
                        engine.run_tool(name, json.loads(arguments)))
    answer = engine.run_agent([{"role": "user", "content": "push my branch"}], max_turns=5,
                              on_escalation=lambda name, result: False, dynamic_turns=False)
    told = str(calls[-1][-1])
    assert "Permission denied by the user, so git_push did not run" in told
    assert "readonly" not in told
    assert answer.startswith("Committed, but the push was declined.")
