"""A context-overflow rejection gets one shrink-and-retry, then plain guidance.

The chars/token estimate can undershoot (code, JSON), and the server's
"maximum context length" 400 used to end the turn with the raw SDK text.
"""
import httpx
import openai

REQ = httpx.Request("POST", "http://gpu:8000/v1/chat/completions")


def _response(content="done"):
    message = type("Message", (), {"content": content, "tool_calls": None})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    return type("Response", (), {"choices": [choice]})()


def _overflow():
    return openai.BadRequestError(
        "This model's maximum context length is 32768 tokens. However, you requested 40000 tokens.",
        response=httpx.Response(400, request=REQ), body=None)


def _history(engine):
    big = "x" * 5000
    msgs = [{"role": "user", "content": "please fix the build"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": f"call {i}"})
        msgs.append({"role": "user", "content": f"{engine._TOOL_RESULT_PREFIX}execute_shell):\n{big}"})
    return msgs


def test_overflow_compacts_and_retries_once_with_a_smaller_budget(engine, monkeypatch):
    calls, compactions = [], []

    def fake_completion(messages, tools, **kw):
        calls.append(kw["max_tokens"])
        if len(calls) == 1:
            raise _overflow()
        return _response("recovered")

    def fake_compact(messages, **kw):
        compactions.append(1)
        messages[:] = [{"role": "system", "content": "Conversation summary:\ns"}, *messages[-2:]]
        return True

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_completion)
    monkeypatch.setattr(engine, "compact_messages", fake_compact)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a: (32768, 4000))
    monkeypatch.setattr(engine, "COMPACTION_THRESHOLD_PCT", 0)  # only the recovery compacts
    trace = []
    answer = engine.run_agent(_history(engine), max_turns=1, trace=trace,
                              provider_name="vllm", model_name="m")
    assert answer == "recovered"
    assert compactions == [1]
    assert len(calls) == 2 and calls[1] <= calls[0] // 2 + 1
    assert {"turn": 0, "type": "context_overflow_recovery", "ok": True} in trace


def test_failed_compaction_drops_old_tool_output_instead(engine, monkeypatch):
    seen = []

    def fake_completion(messages, tools, **kw):
        seen.append([m["content"] for m in messages])
        if len(seen) == 1:
            raise _overflow()
        return _response("recovered")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_completion)
    monkeypatch.setattr(engine, "compact_messages",
                        lambda *a, **k: (_ for _ in ()).throw(_overflow()))
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a: (32768, 4000))
    assert engine.run_agent(_history(engine), max_turns=1, provider_name="vllm",
                            model_name="m") == "recovered"
    retried = seen[1]
    assert sum(engine._DROPPED_TOOL_OUTPUT in text for text in retried) >= 4
    assert retried[0] == "please fix the build"  # the human's words are never cut


def test_a_second_overflow_gives_guidance_not_a_loop(engine, monkeypatch):
    calls = []

    def fake_completion(messages, tools, **kw):
        calls.append(1)
        raise _overflow()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_completion)
    monkeypatch.setattr(engine, "compact_messages", lambda *a, **k: False)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a: (32768, 4000))
    answers = []
    answer = engine.run_agent(_history(engine), max_turns=3, provider_name="vllm",
                              model_name="m", on_answer=answers.append)
    assert len(calls) == 2
    assert answer.startswith("I could not answer: The conversation is too long")
    assert "/compact" in answer and "/reset" in answer
    assert "provider.vllm.context_window" in answer
    assert answers == [answer]


def test_hard_errors_read_as_guidance(engine, monkeypatch):
    def refused(messages, tools, **kw):
        try:
            raise httpx.ConnectError("[Errno 61] Connection refused")
        except httpx.ConnectError as cause:
            raise openai.APIConnectionError(request=REQ) from cause

    monkeypatch.setattr(engine, "_create_completion_with_fallback", refused)
    monkeypatch.setitem(engine.PROVIDERS, "ollama",
                        {"base_url": "http://localhost:11434/v1", "model": "m"})
    answer = engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1,
                              provider_name="ollama", model_name="m")
    assert answer.startswith("I could not answer: Can't reach http://localhost:11434/v1")
    assert "ollama serve" in answer
    assert answer.endswith("Run /doctor to check your setup.")


def test_retry_notices_reach_on_result_when_no_on_status(engine, monkeypatch):
    def fake_completion(messages, tools, on_retry=None, **kw):
        on_retry("Model call failed (429); retrying in 1s -- attempt 2/4")
        return _response("ok")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_completion)
    results, statuses = [], []
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1,
                     on_result=lambda name, out: results.append((name, out)))
    assert results == [("error", "Model call failed (429); retrying in 1s -- attempt 2/4")]
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1,
                     on_result=lambda name, out: results.append((name, out)),
                     on_status=statuses.append)
    assert len(results) == 1 and len(statuses) == 1
