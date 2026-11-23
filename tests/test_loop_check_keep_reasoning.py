"""A reply that repeats itself is stopped; one cut off at the cap while still thinking is kept, not thrown away.

On Qwen3.8-27B, a length cut-off threw the whole reply away and retried smaller: 179 of 3,669 calls in one
long evaluation run, about 1.65M output tokens and 4.6 hours, and most of that was long reasoning that would have
finished, not loops. A cut-off alone cannot tell the two apart; repetition can. Ordinary reasoning stayed at a zlib
compression ratio of at most 3.8 and a unique 8-word share of at least 0.84 on its last 6,000 characters; the loop
measured 12.0 and 0.15. A loop is confirmed only past 6 and 0.6, on two checks in a row.
"""
from pathlib import Path
from types import SimpleNamespace as N

import pytest

from agent8088 import loop_check

ROOT = Path(__file__).resolve().parent.parent
LOOP = "Let me re-check the totals in the sheet once more before I write the answer. " * 700


def _long_documents():
    """Long ordinary text from the repository itself: the wiki, the READMEs and the bundled prompts."""
    paths = sorted(ROOT.glob("docs/**/*.md")) + sorted(ROOT.glob("*.md")) + [
        ROOT / "src" / "agent8088" / "system.md", ROOT / "bench" / "README.md"]
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in paths if p.is_file()]
    return [t for t in texts if len(t) > 20000]


# ------------------------------------------------------------------------------------------------ the detector

def test_a_loop_is_confirmed():
    found = loop_check.LoopWatch().scan(LOOP, "")
    assert found and found["in"] == "reasoning"
    assert found["reasoning"]["cr_tail"] > 6 and found["reasoning"]["u8_tail"] < 0.6


def test_no_false_alarm_on_long_ordinary_text():
    documents = _long_documents()
    assert len(documents) >= 5
    for text in documents:
        watch = loop_check.LoopWatch()
        assert watch.scan(text, "") is None, text[:80]
        assert watch.checks   # it was long enough to be checked


def test_one_repetitive_stretch_is_not_enough():
    # A pasted log or a table can look repetitive for one window; a loop stays that way.
    prose = _long_documents()[0]
    burst = ("same line again " * 600)[:6000]
    watch = loop_check.LoopWatch(start_chars=6000, every_chars=6000)   # one check falls on the stretch
    found = watch.scan(prose[:12000] + burst + prose[12000:40000], "")
    assert found is None
    assert sum(1 for c in watch.checks if c["looping"]) == 1


def test_the_text_and_the_reasoning_are_judged_apart():
    documents = _long_documents()
    found = loop_check.LoopWatch().scan(documents[0][:40000], LOOP)
    assert found and found["in"] == "content"


def test_small_models_get_a_dithering_count_that_does_not_act():
    wandering = " ".join(["Wait, maybe the file is elsewhere. Actually, let me try again with the other path."] * 20
                         + [d[:20000] for d in _long_documents()[:1]])
    watch = loop_check.LoopWatch("sensitive")
    assert watch.scan(wandering, "") is None
    assert all("dither_per_1k_words" in c["reasoning"] for c in watch.checks if "reasoning" in c)


# ----------------------------------------------------------------------------------------- stopped mid-stream

class _Stream:
    def __init__(self, pieces):
        self.pieces, self.sent, self.closed = pieces, 0, False

    def __iter__(self):
        for piece in self.pieces:
            self.sent += 1
            yield N(choices=[N(delta=N(content=None, reasoning_content=piece, tool_calls=None),
                               finish_reason=None)], usage=None)
        yield N(choices=[N(delta=N(content=None, reasoning_content=None, tool_calls=None),
                           finish_reason="length")], usage=None)

    def close(self):
        self.closed = True


class _Client:
    def __init__(self, stream):
        self.stream = stream
        self.chat = N(completions=N(create=lambda **kw: stream))


def _stream_call(engine, monkeypatch, text):
    stream = _Stream([text[i:i + 500] for i in range(0, len(text), 500)])
    monkeypatch.setattr(engine, "_provider_extra_body", lambda *a, **k: {})
    engine._stream_watch.watch = loop_check.LoopWatch()
    try:
        response = engine._create_completion(_Client(stream), [{"role": "user", "content": "x"}], [],
                                             max_tokens=20000, on_token=lambda kind, piece: None,
                                             model_name="qwen3.8-27b", provider_name="vllm")
    finally:
        engine._stream_watch.watch = None
    return response, stream


def test_a_looping_stream_is_stopped_early(engine, monkeypatch):
    response, stream = _stream_call(engine, monkeypatch, LOOP)
    assert response.choices[0].finish_reason == "loop"
    assert stream.sent < len(stream.pieces) / 2          # it did not run to the cap


def test_an_ordinary_stream_runs_to_its_end(engine, monkeypatch):
    response, stream = _stream_call(engine, monkeypatch, _long_documents()[0][:60000])
    assert response.choices[0].finish_reason == "length"
    assert stream.sent == len(stream.pieces)


# ---------------------------------------------------------------------------------------------- in the loop

def _reply(text="", finish="stop", reasoning=""):
    message = N(content=text, tool_calls=None, reasoning_content=reasoning)
    return N(choices=[N(message=message, finish_reason=finish)], usage=None)


def _run(engine, monkeypatch, replies, **flags):
    calls = []

    def fake(messages, tools, max_tokens=None, **kw):
        calls.append({"cap": max_tokens, "thinking": kw.get("thinking"), "messages": list(messages)})
        return replies[min(len(calls) - 1, len(replies) - 1)]

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
    monkeypatch.setattr(engine, "exec_tool", lambda name, arguments, depth=0: "ok")
    for flag in ("_INITIAL_CAP_SET", "_MIN_TOKENS_SET", "_THINKING_OFF_SET", "_ADAPTIVE_SET"):
        monkeypatch.setattr(engine, flag, False)
    monkeypatch.setattr(engine, "OUTPUT_PROFILES", True)
    monkeypatch.setattr(engine, "MODEL_NAME", "qwen3.8-27b")
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a, **kw: (65536, 65000))
    monkeypatch.setattr(engine, "_estimate_tokens", lambda *a: 64)
    for key, value in flags.items():
        monkeypatch.setattr(engine, key, value)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "work out the totals"}], max_turns=8, tools_def=[],
                              allowed_tools={"execute_shell"}, trace=trace)
    return calls, trace, answer


def _thinking():
    return _long_documents()[0][:30000]


def test_long_thinking_cut_off_at_the_cap_is_kept(engine, monkeypatch):
    calls, trace, answer = _run(engine, monkeypatch, [_reply("", "length", _thinking()), _reply("The totals are done.")])
    assert answer.startswith("The totals are done.")
    kept = [s for s in trace if s.get("type") == "cutoff_reasoning_kept"]
    assert len(kept) == 1
    # The next call keeps the full cap and thinking on, and sees how its reasoning ended.
    assert calls[1]["cap"] == 20000 and calls[1]["thinking"] is None
    note = calls[1]["messages"][-1]["content"]
    assert "not repeating itself" in note and _thinking()[-200:].strip() in note
    assert not [s for s in trace if s.get("type") == "loop_stopped"]


def test_a_second_cut_off_at_the_same_step_takes_the_old_ladder(engine, monkeypatch):
    calls, trace, _answer = _run(engine, monkeypatch, [_reply("", "length", _thinking()),
                                                      _reply("", "length", _thinking()), _reply("done")])
    assert len([s for s in trace if s.get("type") == "cutoff_reasoning_kept"]) == 1
    assert calls[2]["thinking"] == "length_retry"           # the old ladder: thinking off


def test_a_confirmed_loop_is_thrown_away(engine, monkeypatch):
    calls, trace, answer = _run(engine, monkeypatch, [_reply("", "length", LOOP), _reply("done")])
    assert answer.startswith("done")
    assert [s["in"] for s in trace if s.get("type") == "loop_stopped"] == ["reasoning"]
    assert not [s for s in trace if s.get("type") == "cutoff_reasoning_kept"]
    note = calls[1]["messages"][-1]["content"]
    assert "repeating itself" in note and LOOP[:60] not in note       # the loop is not handed back
    assert calls[1]["thinking"] == "length_retry" and calls[1]["cap"] <= 8192


def test_a_loop_stopped_mid_stream_is_handled_like_one_found_at_the_cap(engine, monkeypatch):
    _calls, trace, _answer = _run(engine, monkeypatch, [_reply("", "loop", LOOP[:40000]), _reply("done")])
    assert len([s for s in trace if s.get("type") == "loop_stopped"]) == 1


def test_a_tool_call_cut_off_still_earns_a_larger_cap(engine, monkeypatch):
    call = '✿FUNCTION✿: write_file ✿ARGS✿: {"filename": "a.txt", "content": "' + "x" * 3000
    calls, trace, _answer = _run(engine, monkeypatch, [_reply(call, "length"), _reply("done")])
    assert not [s for s in trace if s.get("type") == "cutoff_reasoning_kept"]
    assert calls[1]["cap"] >= 20000


@pytest.mark.parametrize("flags, kept, looped", [
    ({"CUTOFF_KEEP_REASONING": False}, 0, 0),
    ({"LOOP_CHECK": False}, 1, 0),
])
def test_both_behaviours_can_be_turned_off(engine, monkeypatch, flags, kept, looped):
    reasoning = LOOP if flags.get("LOOP_CHECK") is False else _thinking()
    _calls, trace, _answer = _run(engine, monkeypatch, [_reply("", "length", reasoning), _reply("done")], **flags)
    assert len([s for s in trace if s.get("type") == "cutoff_reasoning_kept"]) == kept
    assert len([s for s in trace if s.get("type") == "loop_stopped"]) == looped
