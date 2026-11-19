"""agent8088 0.2.3 fixes found in the Terminal-Bench runs (BUG_REPORT.md #2-#6).

Each test names the bug it covers. Defaults are unchanged for normal use; the
benchmark profile sets chars_per_token / tool_result_max_chars explicitly.
"""
import json


# --- #4: tool-result window is a setting (3,000 default; benchmark 10,000) -----
def test_tool_result_window_defaults_to_3000_and_can_be_raised(engine, monkeypatch):
    assert engine.TOOL_RESULT_MAX_CHARS == 3000          # agent8088's own default
    out = "x" * 9_000
    assert "characters elided" in engine._tool_result_for_model("execute_shell", out)
    monkeypatch.setattr(engine, "TOOL_RESULT_MAX_CHARS", 10_000)   # the benchmark profile
    assert engine._tool_result_for_model("execute_shell", out) == out
    shown = engine._tool_result_for_model("execute_shell", "y" * 30_000)
    assert "20000 characters elided" in shown and len(shown) < 10_300


# --- #2: token estimate uses chars_per_token (4 default; benchmark 2.8) --------
def test_token_estimate_follows_chars_per_token(engine, monkeypatch):
    assert engine._estimate_tokens(4_000) == 1_000
    monkeypatch.setattr(engine, "CHARS_PER_TOKEN", 2.8)
    assert engine._estimate_tokens(2_800) == 1_000


# --- #3: the compaction summary runs without thinking and is counted ----------
def test_thinking_false_turns_off_the_chat_template_thinking(engine, monkeypatch):
    sent = {}

    class Boom(Exception):
        pass

    class Completions:
        def create(self, **options):
            sent.update(options)
            raise Boom("stop here")

    client = type("C", (), {"chat": type("Ch", (), {"completions": Completions()})()})()
    monkeypatch.setattr(engine, "_provider_extra_body", lambda _p: {
        "chat_template_kwargs": {"enable_thinking": True}, "reasoning_effort": "medium", "top_k": 20})
    try:
        engine._create_completion(client, [{"role": "user", "content": "hi"}], [],
                                  provider_name="vllm", thinking=False)
    except Boom:
        pass
    body = sent["extra_body"]
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert "reasoning_effort" not in body and body["top_k"] == 20


def test_compaction_summary_is_thinking_free_and_charged_to_the_budget(engine, monkeypatch):
    seen = {}

    def fake_create(client, messages, tools, **kw):
        seen.update(kw)
        usage = type("U", (), {"prompt_tokens": 1200, "completion_tokens": 300})()
        msg = type("M", (), {"content": "Goal: X. Done: Y."})()
        return type("R", (), {"choices": [type("Ch", (), {"message": msg})()], "usage": usage})()

    monkeypatch.setattr(engine, "create_completion", fake_create)
    budget = engine._TurnBudget()
    messages = [{"role": "user", "content": "task"}] + [
        {"role": "assistant" if i % 2 else "user", "content": f"m{i}"} for i in range(10)]
    assert engine.compact_messages(messages, budget=budget)
    assert seen["thinking"] is False
    assert (budget.input_tokens, budget.output_tokens) == (1200, 300)


# --- #6: one malformed tool call is corrected, not treated as a loop ----------
def _tool_call(name, raw_arguments):
    call = type("T", (), {"function": type("F", (), {"name": name, "arguments": raw_arguments})()})()
    message = type("M", (), {"content": "", "tool_calls": [call]})()
    return type("R", (), {"choices": [type("C", (), {"message": message, "finish_reason": "tool_calls"})()]})()


def _answer(text):
    message = type("M", (), {"content": text, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": message, "finish_reason": "stop"})()]})()


def test_one_malformed_call_does_not_trigger_stop_using_tools(engine, monkeypatch):
    """The cobol-modernization pattern: one bad JSON call, then a good one."""
    script = iter([
        _tool_call("execute_shell", '{"command": "echo "quoted" && ls"}'),   # invalid JSON
        _tool_call("execute_shell", json.dumps({"command": "echo ok"})),
        _answer("done"),
    ])
    monkeypatch.setattr(engine, "_create_completion_with_fallback", lambda *a, **k: next(script))
    ran = []
    real_run_tool = engine.run_tool
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **k: (
        real_run_tool(name, args, **k) if args.get("__parse_error__") else (ran.append(args) or "ok")))
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    messages = [{"role": "user", "content": "run it"}]
    trace = []
    answer = engine.run_agent(messages, max_turns=8, trace=trace)
    texts = [str(m.get("content")) for m in messages]
    assert not any("keep repeating tool calls without progress" in t for t in texts)
    assert ran == [{"command": "echo ok"}] and "done" in answer
    assert any(s.get("type") == "tool_arg_parse_error" for s in trace)


def test_endless_malformed_calls_still_end_the_run(engine, monkeypatch):
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **k: _tool_call("execute_shell", '{"command": "broken'))
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    calls = []
    real = engine._create_completion_with_fallback
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    engine.run_agent([{"role": "user", "content": "run it"}], max_turns=30)
    assert len(calls) <= engine.MALFORMED_CALL_RETRIES + 4


# --- #5: denoise keeps trailing spaces (fixed-width data) -----------------------
def test_denoise_keeps_trailing_spaces_but_still_cleans_noise():
    from agent8088 import tool_output
    record = "U001John Doe            0000001180"
    padded = "B001Python Basics       U002    "
    assert tool_output.denoise(f"{record}\n{padded}") == f"{record}\n{padded}"
    assert tool_output.denoise("\x1b[31mFAILED\x1b[0m tests") == "FAILED tests"
    assert tool_output.denoise("a\n\n  \n\nb") == "a\n\nb"


def test_a_failed_compaction_waits_before_retrying(engine, monkeypatch):
    """#3: an empty summary used to be retried every single turn."""
    attempts = []
    monkeypatch.setattr(engine, "COMPACTION_THRESHOLD_PCT", 50)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a: (1000, 100))
    monkeypatch.setattr(engine, "_estimate_context_chars", lambda *a: 10_000)   # always over
    monkeypatch.setattr(engine, "compact_messages", lambda msgs, **k: attempts.append(1) or False)
    replies = iter([_tool_call("execute_shell", json.dumps({"command": f"echo {i}"})) for i in range(7)]
                   + [_answer("done")])
    monkeypatch.setattr(engine, "_create_completion_with_fallback", lambda *a, **k: next(replies))
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **k: f"out {args}")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    trace = []
    engine.run_agent([{"role": "user", "content": "go"}] * 9, max_turns=20, trace=trace)
    # 8 rounds, a retry every COMPACTION_RETRY_TURNS (3) rounds: at most 3 tries, not 8.
    assert 1 <= len(attempts) <= 3
    assert all(s["ok"] is False for s in trace if s.get("type") == "compaction")
