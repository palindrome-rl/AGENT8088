"""A tool call whose arguments simply stop is a reply that was cut short, not a quoting mistake."""
import json

from test_cutoff_recovery import Model, _call, _reply

CUT = '{"filename": "E:\\\\work\\\\game.js", "mode": "append"'


def test_arguments_that_stop_are_marked_as_cut_off(engine):
    args = engine._parse_error_args(CUT)
    assert args.get("__parse_error_cut_off__") is True


def test_an_unterminated_string_is_cut_off(engine):
    assert engine._parse_error_args('{"filename": "a.js", "content": "line one\\nline').get(
        "__parse_error_cut_off__") is True


def test_a_real_quoting_mistake_is_not_cut_off(engine):
    args = engine._parse_error_args('{"filename": "a.js", "content": "say "hi" now"}')
    assert "__parse_error_at__" in args and "__parse_error_cut_off__" not in args


def test_the_model_is_told_the_reply_was_cut_short_not_to_escape_quotes(engine, monkeypatch):
    first = _reply("", "tool_calls", tool_calls=[_call("write_file", CUT)])
    real = engine.exec_tool
    model = Model(engine, monkeypatch, [first, _reply("done")])
    monkeypatch.setattr(engine, "exec_tool", real)
    trace = []
    engine.run_agent([{"role": "user", "content": "build"}], max_turns=4, tools_def=[],
                     allowed_tools={"write_file"}, trace=trace)
    note = model.sent[1]
    assert "arrived incomplete" in note and "cut short" in note
    assert "double quote" not in note
    assert 'mode=\"append\"' in note or 'mode="append"' in note
    assert any(t.get("type") == "tool_arg_cut_off" for t in trace)


def test_the_cut_off_message_and_trace_say_how_much_arrived(engine, monkeypatch):
    first = _reply("", "tool_calls", tool_calls=[_call("write_file", CUT)])
    real = engine.exec_tool
    model = Model(engine, monkeypatch, [first, _reply("done")])
    monkeypatch.setattr(engine, "exec_tool", real)
    trace = []
    engine.run_agent([{"role": "user", "content": "build"}], max_turns=4, tools_def=[],
                     allowed_tools={"write_file"}, trace=trace)
    assert f"About {len(CUT)} characters arrived" in model.sent[1]
    event = next(t for t in trace if t.get("type") == "tool_arg_cut_off")
    assert event["chars"] == len(CUT) and event["tail"] == CUT[-80:]
