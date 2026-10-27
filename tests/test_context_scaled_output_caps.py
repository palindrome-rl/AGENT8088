"""The first-call output cap, its floor and the thinking-off cap follow the context window unless set.

Seen in runs of a 27B reasoning model on a 64K context: the fixed 8192 first-call cap fell to the 4096 floor after a few short
replies, thinking used the whole allowance, and replies came back empty or file writes were cut off (about 14% of calls)."""
import pytest


@pytest.mark.parametrize("context, expected", [
    (8192, (2048, 2048, 2048)),
    (16384, (4096, 2048, 2048)),
    (32768, (8192, 4096, 4096)),         # the old fixed values
    (65536, (16384, 8192, 8192)),
    (131072, (16384, 8192, 8192)),
])
def test_defaults_follow_the_context_window(engine, context, expected):
    assert engine._output_cap_defaults(context) == expected


def test_an_unknown_context_keeps_the_old_values(engine):
    assert engine._output_cap_defaults(0) == (8192, 4096, 4096)


def test_the_floor_never_exceeds_the_first_call_cap(engine, monkeypatch):
    monkeypatch.setattr(engine, "_INITIAL_CAP_SET", True)
    monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", 3000)
    first, floor, off = engine._output_cap_defaults(65536)
    assert (first, floor) == (3000, 3000)


def test_configured_values_win_over_the_scaled_ones(engine, monkeypatch):
    monkeypatch.setattr(engine, "_INITIAL_CAP_SET", True)
    monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", 12000)
    monkeypatch.setattr(engine, "_MIN_TOKENS_SET", True)
    monkeypatch.setattr(engine, "MAIN_LLM_MIN_TOKENS", 5000)
    monkeypatch.setattr(engine, "_THINKING_OFF_SET", True)
    monkeypatch.setattr(engine, "THINKING_OFF_MAX_TOKENS", 7000)
    assert engine._output_cap_defaults(65536) == (12000, 5000, 7000)


def test_zero_still_means_no_first_call_cap(engine, monkeypatch):
    monkeypatch.setattr(engine, "_INITIAL_CAP_SET", True)
    monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", 0)
    first, floor, off = engine._output_cap_defaults(65536)
    assert first == 0 and floor == 8192 and off == 8192
