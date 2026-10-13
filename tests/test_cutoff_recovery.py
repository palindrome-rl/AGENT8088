"""Recovery from cut-off and empty generations.

A reasoning-only cut-off is usually the model drafting a large file inside its
thinking, so the retry has to say what to do instead. A reply that finishes with
nothing to act on is retried once with thinking off. A partial native tool call
whose name never arrived is still a call being written, not an empty reply.
"""
import json
from types import SimpleNamespace as N

import pytest


def _reply(content="", finish="stop", reasoning="", tool_calls=None):
    message = N(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    return N(choices=[N(message=message, finish_reason=finish)], usage=None)


def _call(name, arguments):
    return N(id="c1", type="function", function=N(name=name, arguments=arguments))


class Model:
    """Scripted completions that record what each call was sent."""

    def __init__(self, engine, monkeypatch, replies):
        self.replies, self.sent, self.thinking, self.caps = list(replies), [], [], []

        def fake(messages, tools, max_tokens=None, **kw):
            self.sent.append(str(messages[-1].get("content") or ""))
            self.thinking.append(kw.get("thinking"))
            self.caps.append(max_tokens)
            return self.replies[min(len(self.sent) - 1, len(self.replies) - 1)]

        monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
        monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a, **k: (131072, 32768))
        monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", 8192)
        monkeypatch.setattr(engine, "exec_tool", lambda name, arguments, depth=0: "ok")


def run(engine, tools=("write_file", "read_text"), trace=None, max_turns=6):
    return engine.run_agent([{"role": "user", "content": "build the app"}], max_turns=max_turns,
                            tools_def=[], allowed_tools=set(tools), trace=trace)


# --- the reasoning-only cut-off ---------------------------------------------------------

def test_a_reasoning_only_cutoff_tells_the_model_not_to_draft_the_file_in_its_thinking(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reply("", "length", reasoning="Next I need to build the app " * 50),
                                        _reply("done")])
    run(engine, tools=("write_file",))
    retry = model.sent[1]
    assert "do not draft its contents in your reasoning" in retry
    assert "first part" in retry and 'mode="append"' in retry


def test_the_hint_is_not_given_when_write_file_is_not_available(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reply("", "length", reasoning="thinking " * 50), _reply("done")])
    run(engine, tools=("read_text",))
    assert "write_file" not in model.sent[1]
    assert "stop reasoning" in model.sent[1]


def test_the_retry_still_runs_with_thinking_off(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reply("", "length", reasoning="x" * 4000), _reply("done")])
    run(engine, tools=("write_file",))
    assert model.thinking == [None, "length_retry"]


def test_a_cutoff_in_prose_is_not_given_the_drafting_hint(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reply("Wait, let me re-check the board. " * 5, "length"), _reply("done")])
    run(engine, tools=("write_file",))
    assert "do not draft" not in model.sent[1]


# --- an empty reply ----------------------------------------------------------------------

def test_a_reply_that_finishes_with_nothing_is_retried_once_with_thinking_off(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reply("", "stop", reasoning="long thought " * 100), _reply("Hello!")])
    answer = run(engine, tools=("read_text",))
    assert answer == "Hello!"
    assert model.thinking == [None, "length_retry"]
    assert "You did not provide an answer" in model.sent[1]


def test_thinking_goes_back_to_normal_after_the_retry(engine, monkeypatch):
    model = Model(engine, monkeypatch, [
        _reply("", "stop"),
        _reply('✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "a.txt"}'),
        _reply("All done."),
    ])
    run(engine, tools=("read_text",))
    assert model.thinking[:3] == [None, "length_retry", None]


def test_an_empty_reply_mid_task_is_told_to_continue_with_thinking_off(engine, monkeypatch):
    model = Model(engine, monkeypatch, [
        _reply('✿FUNCTION✿: write_file ✿ARGS✿: {"filename": "a.py", "content": "x"}'),
        _reply("", "stop"),
        _reply("Done."),
    ])
    trace = []
    def write(name, arguments, depth=0):
        engine.note_mutation(name)
        return "Wrote 1 bytes"

    monkeypatch.setattr(engine, "exec_tool", write)
    run(engine, tools=("write_file",), trace=trace)
    assert any(t.get("type") == "empty_answer" and t.get("thinking_off") for t in trace)
    assert model.thinking[2] == "length_retry"
    assert "Your last reply was empty" in model.sent[2]


# --- a partial native tool call -------------------------------------------------------------

def test_a_partial_call_without_a_name_is_a_call_being_written_not_an_empty_reply(engine, monkeypatch):
    partial = [_call("", '{"filename": "a.py", "content": "def f():\\n    return')]
    model = Model(engine, monkeypatch, [_reply("", "length", tool_calls=partial), _reply("done")])
    trace = []
    run(engine, tools=("write_file",), trace=trace)
    kinds = [t["kind"] for t in trace if t.get("type") == "length_cutoff"]
    assert kinds == ["tool_call"]
    assert 'mode="append"' in model.sent[1] and "do not draft" not in model.sent[1]
    assert model.thinking[1] is None, "a call being written keeps thinking on and gets more room"
    assert model.caps[1] > 4096


def test_a_partial_call_with_a_name_is_still_classed_the_same_way(engine, monkeypatch):
    partial = [_call("write_file", '{"filename": "a.py", "content": "def f():\\n    return')]
    model = Model(engine, monkeypatch, [_reply("", "length", tool_calls=partial), _reply("done")])
    trace = []
    run(engine, tools=("write_file",), trace=trace)
    assert [t["kind"] for t in trace if t.get("type") == "length_cutoff"] == ["tool_call"]


@pytest.mark.parametrize("calls,expected", [
    (None, False),
    ([], False),
    ([_call("", "")], False),
    ([_call("", "{}")], False),
    ([_call("write_file", '{"filename": "a')], True),
    ([_call("", '{"a": 1')], True),
])
def test_has_partial_native_call(engine, calls, expected):
    assert engine._has_partial_native_call(N(tool_calls=calls)) is expected


def test_a_cutoff_with_no_content_and_no_call_is_still_empty(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reply("", "length", reasoning="y" * 4000), _reply("done")])
    trace = []
    run(engine, tools=("write_file",), trace=trace)
    assert [t["kind"] for t in trace if t.get("type") == "length_cutoff"] == ["empty"]
