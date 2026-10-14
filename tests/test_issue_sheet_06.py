"""Issue sheet S06: plan steps reach the explore and coder profiles.

Before this change every plan step ran inline through run_tool, so the explore
and coder profiles in agents/ were never reached; only the auditor and the
test-writer were ever spawned. These tests pin the routing rule and the
fallback to inline execution. The sub-agent itself is stubbed: what is under
test is which profile a step goes to, and what happens when that run fails.
"""
import pytest


@pytest.fixture
def plan_env(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, 'PLAN_AUDIT', False)
    monkeypatch.setattr(engine, 'PERMISSION_MODE', 'full-auto')
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [tmp_path])
    calls = {'subagent': [], 'inline': []}

    def subagent(args, depth=0, **kwargs):
        calls['subagent'].append(args['agent_type'])
        return calls.get('subagent_answer', 'done')

    def inline(name, args, **kwargs):
        calls['inline'].append(name)
        return calls.get('inline_answer', 'inline ok')

    monkeypatch.setattr(engine, '_exec_subagent', subagent)
    monkeypatch.setattr(engine, 'run_tool', inline)
    return engine, calls, tmp_path


@pytest.mark.parametrize('tool,args,expected', [
    ('read_text', {'filename': 'a.py'}, 'explore'),
    ('write_file', {'filename': 'a.py', 'content': 'x'}, 'coder'),
    ('calculate', {'expression': '1+1'}, ''),
    ('execute_shell', {'command': 'rm -rf build && mkdir build'}, ''),
])
def test_plan_step_profile_follows_whether_the_step_mutates(engine, tool, args, expected):
    assert engine._plan_step_profile(tool, args) == expected


def test_read_step_runs_in_explore_not_inline(plan_env):
    engine, calls, tmp_path = plan_env
    result = engine._exec_plan({'steps': [{'tool': 'read_text', 'arguments': {
        'filename': str(tmp_path / 'a.py')}, 'step': 'Read the module'}]})
    assert calls['subagent'] == ['explore'], result
    assert calls['inline'] == [], result


def test_write_step_runs_in_coder_not_inline(plan_env):
    engine, calls, tmp_path = plan_env
    result = engine._exec_plan({'steps': [{'tool': 'write_file', 'arguments': {
        'filename': str(tmp_path / 'a.py'), 'content': 'x'}, 'step': 'Write a.py'}]})
    assert calls['subagent'] == ['coder'], result
    assert calls['inline'] == [], result


@pytest.mark.parametrize('answer', [
    'Error: sub-agent timed out',
    'I could not answer: the model backend errored (Connection error.).',
])
def test_failed_subagent_falls_back_to_inline(plan_env, answer):
    engine, calls, tmp_path = plan_env
    calls['subagent_answer'] = answer
    result = engine._exec_plan({'steps': [{'tool': 'read_text', 'arguments': {
        'filename': str(tmp_path / 'a.py')}, 'step': 'Read the module'}]})
    assert calls['subagent'] == ['explore'], result
    assert calls['inline'] == ['read_text'], result
    assert 'inline ok' in result


def test_step_with_no_profile_stays_inline(plan_env):
    engine, calls, tmp_path = plan_env
    result = engine._exec_plan({'steps': [{'tool': 'calculate', 'arguments': {
        'expression': '1+1'}, 'step': 'Add numbers'}]})
    assert calls['subagent'] == [], result
    assert calls['inline'] == ['calculate'], result
