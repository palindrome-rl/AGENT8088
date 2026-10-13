"""Sheet #37: every call in a reply is kept and answered; endless rewrites are bounded."""
import json

import pytest


def _names(calls):
    return [c["name"] for c in calls]


def test_marker_format_keeps_every_call_in_order(engine):
    text = ('✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "a.py"}\n'
            '✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "b.py"}\n'
            '✿FUNCTION✿: execute_shell ✿ARGS✿: {"command": "ls"}')
    calls = engine.find_tool_calls(text)
    assert _names(calls) == ["read_text", "read_text", "execute_shell"]
    assert [c["arguments"].get("filename") for c in calls[:2]] == ["a.py", "b.py"]


def test_bare_json_format_keeps_every_call(engine):
    text = ('{"name": "read_text", "arguments": {"filename": "a.py"}}\n'
            '{"name": "read_text", "arguments": {"filename": "b.py"}}')
    calls = engine.find_tool_calls(text)
    assert [c["arguments"]["filename"] for c in calls] == ["a.py", "b.py"]


def test_mixed_valid_and_invalid_calls_keep_the_valid_ones(engine):
    text = ('✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "a.py"}\n'
            '✿FUNCTION✿: not_a_tool ✿ARGS✿: {"x": 1}\n'
            '✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "b.py"')
    calls = engine.find_tool_calls(text)
    assert [c["name"] for c in calls] == ["read_text", "read_text"]
    assert calls[0]["arguments"] == {"filename": "a.py"}
    assert "__parse_error__" in calls[1]["arguments"]


def _write_response(path, body):
    call = type("T", (), {"function": type("F", (), {
        "name": "write_file",
        "arguments": json.dumps({"filename": path, "content": body})})()})()
    message = type("M", (), {"content": "", "tool_calls": [call]})()
    return type("R", (), {"choices": [type("C", (), {"message": message,
                                                     "finish_reason": "tool_calls"})()]})()


def test_endless_whole_file_rewrites_are_bounded(engine, monkeypatch):
    writes, count = [], [0]

    def model(messages, tools, **kwargs):
        count[0] += 1
        return _write_response("script.py", f"print({count[0]})\n")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **kw: writes.append(args) or "Wrote")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "MAX_FILE_REWRITES", 6)
    trace = []
    engine.run_agent([{"role": "user", "content": "make script.py work"}], max_turns=30,
                     trace=trace, dynamic_turns=False)
    assert len(writes) == 6
    assert any(t["type"] == "rewrite_limit" for t in trace)
