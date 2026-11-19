"""Regression coverage for schema unions, smoke isolation and real SDK stalls."""
import threading
import time
from types import SimpleNamespace

import pytest

from agent8088 import app_check


@pytest.mark.parametrize('value', ['hello', None])
def test_nullable_mcp_schema_is_valid(engine, value):
    spec = {'mode': 'mcp', 'parameters': {
        'type': 'object', 'required': ['query'],
        'properties': {'query': {'type': ['string', 'null']}}}}
    assert engine._validate_tool_args('mcp_example', spec, {'query': value}) == ''


def test_timeout_cleanup_does_not_kill_another_browser(monkeypatch):
    import psutil
    killed = []
    unrelated = SimpleNamespace(create_time=lambda: time.time(), name=lambda: 'chrome.exe',
                                children=lambda recursive=True: [], kill=lambda: killed.append('unrelated'))
    monkeypatch.setattr(psutil, 'Process', lambda: SimpleNamespace(children=lambda recursive=True: [unrelated]))
    # No browser owned by the expired check is present in out.
    app_check._abandon({}, time.time())
    assert not killed, 'Cleanup killed a browser that the expired check did not create'


def test_timeout_cleanup_stops_only_its_marked_process_tree(monkeypatch):
    import psutil
    killed = []
    marker = '--agent8088-smoke-check=test-owner'
    def process(label, args, children=()):
        return SimpleNamespace(cmdline=lambda: args, children=lambda recursive=True: list(children),
                               kill=lambda: killed.append(label))
    child = process('owned-renderer', ['chrome', '--type=renderer'])
    owned = process('owned-browser', ['chrome', marker], [child])
    unrelated = process('other-browser', ['chrome', '--agent8088-smoke-check=someone-else'])
    monkeypatch.setattr(psutil, 'Process', lambda: SimpleNamespace(
        children=lambda recursive=True: [unrelated, owned, child]))
    cancelled = threading.Event()
    app_check._abandon({'browser_marker': marker, 'cancelled': cancelled}, time.time())
    assert cancelled.is_set()
    assert killed == ['owned-renderer', 'owned-browser']


@pytest.mark.parametrize('schema,valid,invalid', [
    ({'type': ['string', 'null']}, None, []),
    ({'type': ['array', 'object']}, [], False),
    ({'anyOf': [{'type': 'string'}, {'type': 'null'}]}, None, {}),
    ({'oneOf': [{'type': 'integer'}, {'type': 'null'}]}, 3, True),
])
def test_schema_union_validation_preserves_valid_values_and_rejects_wrong_types(engine, schema, valid, invalid):
    spec = {'mode': 'mcp', 'parameters': {'required': ['value'], 'properties': {'value': schema}}}
    assert engine._validate_tool_args('mcp_example', spec, {'value': valid}) == ''
    assert 'invalid arguments' in engine._validate_tool_args('mcp_example', spec, {'value': invalid})
    assert "'value' is required" in engine._validate_tool_args('mcp_example', spec, {})


def test_external_image_does_not_excuse_a_local_javascript_bug(tmp_path):
    if not app_check.chromium_available():
        pytest.skip('Chromium unavailable')
    page = tmp_path / 'index.html'
    page.write_text('''<!doctype html><html><body>
      <img src="https://images.example.com/picture.png">
      <button onclick="document.title='clicked'">Go</button>
      <script>missingLocalFunction()</script>
    </body></html>''', encoding='utf-8')
    report = app_check.check_web_page(page)
    assert report.status == app_check.FAIL, report.text()
    assert any('missingLocalFunction' in item for item in report.failures)


def test_blocked_script_cannot_turn_an_unrelated_bug_into_a_pass(tmp_path):
    if not app_check.chromium_available():
        pytest.skip('Chromium unavailable')
    page = tmp_path / 'index.html'
    page.write_text('''<!doctype html><html><body>
      <script src="https://cdn.example.com/library.js"></script>
      <button onclick="document.title='clicked'">Go</button>
      <script>missingLocalFunction()</script>
    </body></html>''', encoding='utf-8')
    report = app_check.check_web_page(page)
    assert report.status != app_check.PASS, report.text()


def test_real_browser_timeout_preserves_a_concurrent_browser(tmp_path):
    if not app_check.chromium_available():
        pytest.skip('Chromium unavailable')
    from playwright.sync_api import sync_playwright
    page = tmp_path / 'index.html'
    page.write_text('''<!doctype html><html><body>
      <button onclick="while(true){}">Go</button>
    </body></html>''', encoding='utf-8')
    with sync_playwright() as p:
        other = p.chromium.launch()
        try:
            tab = other.new_page()
            report = app_check.check_web_page(page, timeout=.2)
            assert report.status == app_check.UNAVAILABLE, report.text()
            assert tab.evaluate('1+1') == 2, 'A different browser was stopped by smoke cleanup'
        finally:
            other.close()


def test_idle_timeout_returns_when_stream_close_does_not_unblock_reader(engine):
    release = threading.Event()
    finished = threading.Event()
    failures = []

    class Stalled:
        def __iter__(self):
            yield 'first'
            release.wait(3)
        def close(self):
            pass  # Valid iterator close; not every SDK unblocks a concurrent read.

    def consume():
        try:
            list(engine._IdleGuard(Stalled(), .1))
        except engine.ProviderStreamError as error:
            failures.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    try:
        assert finished.wait(.7), 'Idle guard fired but the caller stayed blocked'
        assert failures and failures[0].status_code == 504
    finally:
        release.set()
        thread.join(2)


def test_idle_guard_preserves_errors_and_does_not_time_consumer_work(engine):
    def chunks():
        yield 'one'
        raise ValueError('original error')
    iterator = iter(engine._IdleGuard(chunks(), .05))
    assert next(iterator) == 'one'
    time.sleep(.1)  # work done by the caller is not network silence
    with pytest.raises(ValueError, match='original error'):
        next(iterator)


def test_idle_guard_close_unblocks_its_consumer_before_first_chunk(engine):
    release = threading.Event()
    finished = threading.Event()
    def waiting():
        release.wait(3)
        yield 'too late'
    guard = engine._IdleGuard(waiting(), .1)
    thread = threading.Thread(target=lambda: (list(guard), finished.set()), daemon=True)
    thread.start()
    try:
        guard.close()
        assert finished.wait(.5)
    finally:
        release.set()
        thread.join(2)


def test_page_check_fails_closed_without_websocket_interception(tmp_path, monkeypatch):
    if not app_check.chromium_available():
        pytest.skip('Chromium unavailable')
    from playwright.sync_api import BrowserContext
    monkeypatch.setattr(BrowserContext, 'route_web_socket', None)
    page = tmp_path / 'index.html'
    page.write_text('<h1>Hello</h1>', encoding='utf-8')
    report = app_check.check_web_page(page)
    assert report.status == app_check.UNAVAILABLE
    assert 'WebSocket interception' in report.note


@pytest.mark.parametrize('verdicts,expected,cap,confirm', [
    (['fail', 'pass'], 'new', 100, True),
    (['fail', 'fail'], 'old', 100, True),
    (['fail', 'fail'], 'new', 1, True),
    (['fail'], 'old', 100, False),
])
def test_execute_plan_confirms_failure_before_rollback(engine, monkeypatch, tmp_path, verdicts, expected, cap, confirm):
    monkeypatch.setattr(engine, 'PLAN_AUDIT', True)
    monkeypatch.setattr(engine, 'PLAN_AUDIT_REVERT', True)
    monkeypatch.setattr(engine, 'PLAN_AUDIT_CONFIRM_FAIL', confirm)
    monkeypatch.setattr(engine, 'PLAN_REVERT_MAX_BYTES', cap)
    monkeypatch.setattr(engine, 'PERMISSION_MODE', 'full-auto')
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [tmp_path])
    target = tmp_path / 'result.txt'
    target.write_text('old', encoding='utf-8')
    seen = []
    def audit(*args, **kwargs):
        verdict = verdicts[len(seen)]
        seen.append(args)
        return ('bad write', 'VERDICT: fail') if verdict == 'fail' else ('', engine.AUDIT_PASSED_NOTE)
    monkeypatch.setattr(engine, '_audit_plan_step', audit)
    monkeypatch.setattr(engine, 'run_tool', lambda *a, **k: target.write_text('new', encoding='utf-8') and 'Wrote')
    result = engine._exec_plan({'steps': [{'tool': 'write_file', 'arguments': {
        'filename': str(target), 'content': 'new'}, 'acceptance': 'correct output'}]})
    assert len(seen) == len(verdicts), result
    assert all(args[5] == 'correct output' for args in seen)
    assert target.read_text(encoding='utf-8') == expected, result
    if cap == 1:
        assert 'NOT REVERTED' in result


@pytest.mark.parametrize('backend', ['openai', 'litellm'])
def test_idle_timeout_unblocks_a_real_openai_http_stream(engine, backend):
    import http.server
    import json
    from openai import OpenAI

    release = threading.Event()
    finished = threading.Event()
    first = threading.Event()
    errors = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            payload = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 0,
                       'model': 'test', 'choices': [{'index': 0, 'delta': {'content': 'first'},
                                                   'finish_reason': None}]}
            self.wfile.write(('data: ' + json.dumps(payload) + '\n\n').encode())
            self.wfile.flush()
            release.wait(5)
            try:
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    client = OpenAI(api_key='test-only', base_url=f'http://127.0.0.1:{server.server_port}/v1',
                    timeout=3, max_retries=0)
    if backend == 'litellm':
        import litellm
        stream = litellm.completion(model='openai/test', api_key='test-only',
                                   api_base=f'http://127.0.0.1:{server.server_port}/v1',
                                   messages=[{'role': 'user', 'content': 'hi'}],
                                   stream=True, timeout=3, num_retries=0)
    else:
        stream = client.chat.completions.create(model='test', messages=[{'role': 'user', 'content': 'hi'}],
                                                stream=True)

    def consume():
        try:
            for chunk in engine._IdleGuard(stream, .1):
                first.set()
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    try:
        assert first.wait(2), 'Test server did not deliver the initial SSE event'
        assert finished.wait(.8), f'Real {backend} stream remained blocked beyond its .1s idle limit'
        assert errors and isinstance(errors[0], engine.ProviderStreamError)
    finally:
        release.set()
        thread.join(3)
        client.close()
        server.shutdown()
        server.server_close()
