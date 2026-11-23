"""After a cut-off whose whole budget went to reasoning, the next few ordinary calls
also run with thinking off, at the normal cap, then thinking returns.
"""
import pytest
from test_cutoff_recovery import Model, _reply, run


@pytest.fixture(autouse=True)
def _context_scaled_caps(engine, monkeypatch):
    """These tests cover the sticky calls at the context-scaled caps, which output_profiles=0 keeps; the profile
    rule (a sticky call gets the model's non-thinking cap) is tested in test_model_output_profiles.py."""
    monkeypatch.setattr(engine, "OUTPUT_PROFILES", False)


@pytest.fixture(autouse=True)
def _discard_ladder(engine, monkeypatch):
    """These tests cover the discard-and-retry ladder after a cut-off, which cutoff_keep_reasoning=0 keeps."""
    monkeypatch.setattr(engine, "CUTOFF_KEEP_REASONING", False)


def _read(n):
    # Distinct arguments each time: an identical repeated call ends the run as "no progress".
    return _reply('✿FUNCTION✿: read_text ✿ARGS✿: {"filename": "f%d.txt"}' % n)


def _reasoning_cutoff():
    return _reply("", "length", reasoning="Next I need to build the app " * 50)


def test_the_calls_after_a_reasoning_cutoff_also_run_with_thinking_off(engine, monkeypatch):
    model = Model(engine, monkeypatch, [
        _reasoning_cutoff(), _read(1), _read(2), _read(3), _read(4), _read(5), _reply("done")])
    run(engine, tools=("read_text",), max_turns=10)
    # cut-off, its retry, three sticky calls, then normal thinking again
    assert model.thinking[:6] == [None, "length_retry", "length_retry", "length_retry", "length_retry", None]


def test_a_sticky_call_keeps_the_normal_cap(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reasoning_cutoff(), _read(1), _read(2), _reply("done")])
    run(engine, tools=("read_text",), max_turns=10)
    assert model.caps[1] <= engine.THINKING_OFF_MAX_TOKENS, "the retry itself keeps its small cap"
    assert model.caps[2] > engine.THINKING_OFF_MAX_TOKENS, "a sticky call needs room to write a file"


def test_it_can_be_turned_off(engine, monkeypatch):
    monkeypatch.setattr(engine, "THINKING_OFF_STICKY_CALLS", 0)
    model = Model(engine, monkeypatch, [_reasoning_cutoff(), _read(1), _read(2), _reply("done")])
    run(engine, tools=("read_text",), max_turns=10)
    assert model.thinking[:4] == [None, "length_retry", None, None]


def test_a_cutoff_while_writing_a_call_does_not_start_it(engine, monkeypatch):
    partial = '✿FUNCTION✿: write_file ✿ARGS✿: {"filename": "a", "content": "xxxx'
    model = Model(engine, monkeypatch, [_reply(partial, "length"), _read(1), _read(2), _reply("done")])
    run(engine, tools=("write_file", "read_text"), max_turns=10)
    assert set(model.thinking) == {None}


def test_a_prose_cutoff_does_not_start_it(engine, monkeypatch):
    model = Model(engine, monkeypatch, [_reply("Wait, let me re-check. " * 20, "length"),
                                        _read(1), _read(2), _reply("done")])
    run(engine, tools=("read_text",), max_turns=10)
    assert model.thinking[2:] == [None, None]


def test_the_trace_records_each_sticky_call(engine, monkeypatch):
    Model(engine, monkeypatch, [_reasoning_cutoff(), _read(1), _read(2), _reply("done")])
    trace = []
    run(engine, tools=("read_text",), trace=trace, max_turns=10)
    modes = [t["mode"] for t in trace if t.get("type") == "retry_mode"]
    assert modes.count("sticky_thinking_off") == 2


def test_a_second_reasoning_cutoff_restarts_the_count(engine, monkeypatch):
    model = Model(engine, monkeypatch, [
        _reasoning_cutoff(), _read(1), _read(2), _read(3), _read(4), _read(5),
        _reasoning_cutoff(), _read(6), _reply("done")])
    run(engine, tools=("read_text",), max_turns=14)
    assert model.thinking.count("length_retry") >= 6
