"""A blocked tool with no escalation handler must not leak the wire format.

ESCALATION_REQUEST is an internal protocol string -- unit separators, target
mode, change type, paths -- produced when a tool needs permission. The agent
loop normally intercepts it and asks via on_escalation. But on_escalation
defaults to None, and _exec_subagent passes ui.get("on_escalation"), which is
None for a sub-agent spawned without a UI. On that path the raw payload used
to fall through and get appended as if it were ordinary tool output, which is
how internal protocol text ends up quoted back in a final answer.
"""
import pytest


def _blocked_run(engine, monkeypatch, **kwargs):
    """Run one turn where the only tool call is refused by the permission layer."""
    calls = {"n": 0}

    class _Fn:
        name = "write_file"
        arguments = '{"filename": "x.txt", "content": "hi"}'

    class _ToolCall:
        id = "call_1"
        function = _Fn()
        type = "function"

    class _Msg:
        content = ""
        tool_calls = [_ToolCall()]

    class _Choice:
        message = _Msg()
        finish_reason = "tool_calls"

    class _Resp:
        choices = [_Choice()]

    def fake_completion(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp()
        # Second turn: no tool calls, just answer -- ends the loop.
        class _Done:
            class message:
                content = "done"
                tool_calls = None
            finish_reason = "stop"
        class _R:
            choices = [_Done()]
        return _R()

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    monkeypatch.setattr(engine, "exec_tool",
                        lambda name, arguments, depth=0:
                        "ESCALATION_REQUEST\x1fedit\x1fnew_file\x1fC:\\x.txt\x1fneeds write")

    seen = []
    monkeypatch.setattr(engine, "_tool_result_for_model",
                        lambda name, result: seen.append(result) or result)

    engine.run_agent([{"role": "user", "content": "write a file"}],
                     max_turns=3, **kwargs)
    return seen


def test_raw_escalation_payload_never_reaches_the_model(engine, monkeypatch):
    """With no handler to ask, the model gets a plain refusal -- not the wire
    format it would otherwise be free to echo back at the user."""
    engine.PERMISSION_MODE = "readonly"
    seen = _blocked_run(engine, monkeypatch, on_escalation=None)

    assert seen, "no tool result was fed back to the model at all"
    for result in seen:
        assert "ESCALATION_REQUEST" not in result, (
            "internal escalation wire format leaked into model context")
        assert "\x1f" not in result, "unit separators leaked into model context"

    assert any("Permission denied" in r for r in seen)
    assert any("Do not retry" in r for r in seen)


def test_an_escalation_handler_still_gets_the_real_payload(engine, monkeypatch):
    """The refusal substitution must not fire when someone can actually be
    asked -- on_escalation needs the real payload to render the prompt."""
    engine.PERMISSION_MODE = "readonly"
    handed = []

    def on_escalation(name, result):
        handed.append(result)
        return False  # deny, so the loop moves on rather than retrying

    _blocked_run(engine, monkeypatch, on_escalation=on_escalation)

    assert handed, "on_escalation was never called for a blocked tool"
    assert handed[0].startswith("ESCALATION_REQUEST\x1f"), (
        "the handler must receive the real payload, not the substituted text")
