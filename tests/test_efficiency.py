import json
from agent8088 import efficiency


def test_read_signature_invalidates_and_excludes_side_effects(tmp_path):
    path = tmp_path / 'a.txt'
    path.write_text('one')
    args = {'filename': str(path)}
    resolve = lambda _: path
    first = efficiency.read_signature('read_text', args, resolve, 'readonly')
    assert first == efficiency.read_signature('read_text', args, resolve, 'readonly')
    path.write_text('different contents')
    assert first != efficiency.read_signature('read_text', args, resolve, 'readonly')
    assert first != efficiency.read_signature('read_text', args, resolve, 'full-auto')
    for name in ('write_file', 'execute_shell', 'browse_page', 'web_search'):
        assert efficiency.read_signature(name, args, resolve, 'full-auto') is None


def test_cost_reader_does_not_hide_unknowns(tmp_path):
    path = tmp_path / 'telemetry.jsonl'
    rows = [dict(event='model_call', task_id='a', input_tokens=20, output_tokens=10, cost_usd=.1),
            dict(event='model_call', input_tokens=None, output_tokens=20, cost_usd=.02)]
    path.write_text('\n'.join(map(json.dumps, rows)) + '\npartial record')
    report = efficiency.telemetry_summary(path)
    assert report['known_cost_usd'] == .1
    assert report['unknown_cost_calls'] == 1
    assert report['malformed_records'] == 1
    assert efficiency.telemetry_summary(path, 'a')['calls'] == 1
    assert not efficiency.telemetry_summary(tmp_path / 'absent')['available']


def test_tool_argument_recovery(engine):
    for value in ('{bad', '[]', 'null', '42'):
        result = engine.exec_tool('read_text', value)
        assert result.startswith('Error:')
        assert 'suggested_action' in result
        assert '"recoverable": true' in result
    missing = engine.exec_tool('read_text', '{}')
    assert '"recoverable": true' in missing
    assert 'filename' in missing


def test_read_text_rejects_urls_with_a_browser_hint(engine):
    result = engine.exec_tool('read_text', json.dumps({'filename': 'https://example.com'}))
    assert 'read_text accepts local file paths' in result
    assert "Use browse_page" in result
    assert 'suggested_action' in result


def test_stable_tool_order(engine):
    a = {'args': ['filename'], 'description': 'read', 'mode': 'read_text'}
    forward = {'a': a, 'z': a}
    backward = {'z': a, 'a': a}
    assert engine.render_tool_docs(forward) == engine.render_tool_docs(backward)
    assert engine.build_tools_def(forward) == engine.build_tools_def(backward)


def test_missing_stream_usage_is_not_zero_cost(engine, monkeypatch, tmp_path):
    from types import SimpleNamespace
    path = tmp_path / 'calls.jsonl'
    monkeypatch.setattr(engine, 'MODEL_TELEMETRY_ENABLED', True)
    monkeypatch.setattr(engine, 'MODEL_TELEMETRY_PATH', path)
    monkeypatch.setattr(engine, 'COST_PER_1K_INPUT', 1)
    monkeypatch.setattr(engine, 'COST_PER_1K_OUTPUT', 1)
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='hello'), finish_reason='stop')])
    engine._record_model_telemetry(engine.ACTIVE_PROVIDER, engine.MODEL_NAME, 'primary',
                                  engine.time.monotonic(), max_tokens=10, response=response)
    assert json.loads(path.read_text())['cost_usd'] is None


def test_run_cache_reuses_only_successful_file_read(engine, monkeypatch, tmp_path):
    path = tmp_path / 'a.txt'
    path.write_text('ORCHID')
    args = {'filename': str(path)}
    call = '\u273fFUNCTION\u273f: read_text \u273fARGS\u273f: ' + json.dumps(args)
    responses = iter([call, call, 'done'])
    def completion(*a, **kw):
        from types import SimpleNamespace as NS
        return NS(choices=[NS(message=NS(content=next(responses)), finish_reason='stop')])
    executed = []
    monkeypatch.setattr(engine, '_create_completion_with_fallback', completion)
    monkeypatch.setattr(engine, 'resolve_user_path', lambda _: path)
    monkeypatch.setattr(engine, 'exec_tool', lambda *a, **kw: executed.append(a) or 'ORCHID')
    messages = [{'role':'user', 'content':'Test reads'}]
    assert engine.run_agent(messages, max_turns=4, system_prompt='', tools_def=[],
                            allowed_tools={'read_text'}, memory_capture=False) == 'done'
    assert len(executed) == 1
    assert any('already ran with this output' in m['content'] for m in messages)


def test_stream_usage_is_preserved_and_unsupported_option_retries_once(engine, monkeypatch):
    from types import SimpleNamespace as NS
    usage = NS(prompt_tokens=40, completion_tokens=2)
    calls = []
    class Unsupported(Exception):
        status_code = 400
    def create(**kwargs):
        calls.append(kwargs)
        if 'stream_options' in kwargs:
            raise Unsupported('unsupported parameter stream_options')
        return iter([NS(choices=[NS(delta=NS(content='ok'), finish_reason='stop')]), NS(choices=[], usage=usage)])
    monkeypatch.setattr(engine, 'MODEL_TELEMETRY_ENABLED', True)
    client = NS(chat=NS(completions=NS(create=create)))
    result = engine._create_completion(client, [], [], on_token=lambda *a: None)
    assert len(calls) == 2
    assert 'stream_options' not in calls[1]
    assert result.usage is usage
    assert result.first_token_ms is not None


def test_structured_error_contract_survives_output_truncation(engine):
    """Two features that meet at the same string, from different branches.

    The recovery contract is appended to the end of a tool result; tail-first
    clamping keeps the end. That is why they compose -- but nothing states it,
    so a future change to either side could quietly drop the JSON and leave the
    model with a wall of noise and no suggested_action.
    """
    body = "\n".join(f"line {i}: verbose tool chatter" for i in range(2000))
    error = efficiency.tool_error('invalid_input', body,
                                  'Correct the arguments and retry.', recoverable=True)
    shown = engine._tool_result_for_model("execute_shell", error)

    assert len(shown) < len(error), "nothing was truncated; the test proves nothing"
    assert '"suggested_action"' in shown
    assert '"recoverable": true' in shown
