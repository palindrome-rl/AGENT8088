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


@pytest.mark.parametrize('context, completion, first, floor', [
    (8192, 32768, 2048, 2048), (16384, 32768, 4096, 2048),
    (32768, 32768, 8192, 4096), (65536, 32768, 16384, 8192),
    (131072, 32768, 16384, 8192), (0, 32768, 8192, 4096),
    (65536, 6000, 6000, 6000),
])
def test_the_actual_model_requests_use_the_scaled_caps(engine, monkeypatch, context, completion, first, floor):
    from test_output_cap import Script, _tool_reply, _reply
    model = Script(engine, monkeypatch, [_tool_reply(n, 120) for n in range(5)] + [_reply('done', 50)])
    for flag in ('_INITIAL_CAP_SET', '_MIN_TOKENS_SET', '_THINKING_OFF_SET'):
        monkeypatch.setattr(engine, flag, False)
    monkeypatch.setattr(engine, '_active_model_token_limits', lambda *a, **kw: (context, completion))
    monkeypatch.setattr(engine, '_estimate_tokens', lambda *a: 64)
    engine.run_agent([{'role': 'user', 'content': 'check the data'}], max_turns=12,
                     tools_def=[], allowed_tools={'execute_shell'})
    assert model.caps[:3] == [first] * 3
    assert model.caps[3:6] == [floor] * 3


def test_the_scaled_thinking_off_cap_reaches_the_retry_request(engine, monkeypatch):
    from test_output_cap import Script, _reply
    model = Script(engine, monkeypatch, [_reply('', finish='length', reasoning='thinking ' * 1000), _reply('done')])
    for flag in ('_INITIAL_CAP_SET', '_MIN_TOKENS_SET', '_THINKING_OFF_SET'):
        monkeypatch.setattr(engine, flag, False)
    monkeypatch.setattr(engine, '_active_model_token_limits', lambda *a, **kw: (65536, 32768))
    monkeypatch.setattr(engine, '_estimate_tokens', lambda *a: 64)
    engine.run_agent([{'role': 'user', 'content': 'hello'}], max_turns=5, tools_def=[], allowed_tools=set())
    assert model.caps[:2] == [16384, 8192]


def test_model_context_changes_recompute_the_default(engine, monkeypatch):
    from test_output_cap import Script, _tool_reply, _reply
    model = Script(engine, monkeypatch, [_tool_reply(1, 120), _reply('done')])
    for flag in ('_INITIAL_CAP_SET', '_MIN_TOKENS_SET', '_THINKING_OFF_SET'):
        monkeypatch.setattr(engine, flag, False)
    monkeypatch.setattr(engine, '_active_model_token_limits',
                        lambda *a, **kw: (32768 if not model.caps else 65536, 32768))
    monkeypatch.setattr(engine, '_estimate_tokens', lambda *a: 64)
    engine.run_agent([{'role': 'user', 'content': 'check the data'}], max_turns=5,
                     tools_def=[], allowed_tools={'execute_shell'})
    assert model.caps == [8192, 16384]


def test_zero_disables_adaptive_shrinking_in_actual_requests(engine, monkeypatch):
    from test_output_cap import Script, _tool_reply, _reply
    model = Script(engine, monkeypatch, [_tool_reply(n, 120) for n in range(5)] + [_reply('done', 50)])
    monkeypatch.setattr(engine, '_INITIAL_CAP_SET', True)
    monkeypatch.setattr(engine, 'INITIAL_COMPLETION_CAP', 0)
    monkeypatch.setattr(engine, '_MIN_TOKENS_SET', False)
    monkeypatch.setattr(engine, '_THINKING_OFF_SET', False)
    monkeypatch.setattr(engine, '_active_model_token_limits', lambda *a, **kw: (65536, 32768))
    monkeypatch.setattr(engine, '_estimate_tokens', lambda *a: 64)
    engine.run_agent([{'role': 'user', 'content': 'check the data'}], max_turns=12,
                     tools_def=[], allowed_tools={'execute_shell'})
    assert model.caps == [32768] * 6
