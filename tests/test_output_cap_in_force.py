"""The output cap an ordinary call is actually given is the one the trace and the log name.

Six long build runs were cut off at 16,384 tokens while the announced limit said 20,000: an explicit
initial_completion_cap in config overrode the model profile, as it should, but the trace and the log printed the
profile's value. The cap in force, and where it comes from, are now part of the announcement, and an override is
logged once as a warning so nobody tunes the wrong number.
"""
import logging

import pytest


def _run(engine, monkeypatch, replies, *, initial_cap=None, completion=65000, model_name="qwen3.8-27b"):
    from test_output_cap import Script
    model = Script(engine, monkeypatch, replies)
    for flag in ("_INITIAL_CAP_SET", "_MIN_TOKENS_SET", "_THINKING_OFF_SET", "_ADAPTIVE_SET"):
        monkeypatch.setattr(engine, flag, False)
    if initial_cap is not None:
        monkeypatch.setattr(engine, "_INITIAL_CAP_SET", True)
        monkeypatch.setattr(engine, "INITIAL_COMPLETION_CAP", initial_cap)
    monkeypatch.setattr(engine, "OUTPUT_PROFILES", True)
    monkeypatch.setattr(engine, "MODEL_NAME", model_name)
    monkeypatch.setattr(engine, "_OUTPUT_PROFILE_SEEN", {})
    monkeypatch.setattr(engine, "_CAP_OVERRIDE_WARNED", set())
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a, **kw: (65536, completion))
    monkeypatch.setattr(engine, "_estimate_tokens", lambda *a: 64)
    trace = []
    engine.run_agent([{"role": "user", "content": "check the data"}], max_turns=12, tools_def=[],
                     allowed_tools={"execute_shell"}, trace=trace)
    return model, [s for s in trace if s.get("type") == "output_profile"]


def _replies(n=3):
    from test_output_cap import _reply, _tool_reply
    return [_tool_reply(i, 120, reasoning="checking") for i in range(n)] + [_reply("done", 50)]


def test_the_profile_cap_is_named_as_the_cap_in_force(engine, monkeypatch):
    model, named = _run(engine, monkeypatch, _replies())
    assert model.caps[0] == 20000
    assert named[0]["cap_in_force"] == 20000 and named[0]["cap_source"] == "model profile"
    assert "cap in force 20000" in named[0]["content"]


def test_a_config_override_is_what_the_trace_names(engine, monkeypatch):
    model, named = _run(engine, monkeypatch, _replies(), initial_cap=16384)
    assert model.caps[0] == 16384
    assert named[0]["cap_in_force"] == 16384
    assert named[0]["cap_source"] == "initial_completion_cap in config"
    assert named[0]["thinking_cap"] == 20000   # the profile's own value is still recorded, under its own name
    assert "cap in force 16384 (initial_completion_cap in config)" in named[0]["content"]


def test_a_config_override_is_warned_about_once(engine, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=engine._log.name):
        _run(engine, monkeypatch, _replies(4), initial_cap=16384)
    warnings = [r.getMessage() for r in caplog.records if "overrides" in r.getMessage()]
    assert len(warnings) == 1
    assert "initial_completion_cap=16384" in warnings[0] and "20000" in warnings[0]


def test_no_warning_when_the_config_agrees_with_the_profile(engine, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=engine._log.name):
        _run(engine, monkeypatch, _replies(), initial_cap=20000)
    assert not [r for r in caplog.records if "overrides" in r.getMessage()]


def test_a_lower_completion_limit_is_named_as_the_source(engine, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=engine._log.name):
        model, named = _run(engine, monkeypatch, _replies(), completion=8192)
    assert model.caps[0] == 8192
    assert named[0]["cap_in_force"] == 8192 and named[0]["cap_source"] == "the model's completion limit"
    assert any("8192" in r.getMessage() and "20000" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("initial, limit, expected", [
    (20000, 65000, (20000, "model profile")),
    (0, 65000, (65000, "the model's completion limit")),
    (30000, 8192, (8192, "the model's completion limit")),
])
def test_cap_in_force_picks_the_lower_and_says_why(engine, initial, limit, expected):
    assert engine._cap_in_force(initial, limit, profile_set=True, config_set=False) == expected
