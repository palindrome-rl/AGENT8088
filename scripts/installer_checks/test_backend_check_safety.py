"""Host-side discovery/probing must not expand the sandbox's authority."""
import http.server
import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent8088 import backend_check as bc
from agent8088.trajectory import TrajectoryState


def test_probe_does_not_follow_project_controlled_redirects():
    hits = []

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass

    other = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Other)

    class Redirect(Other):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{other.server_port}/unrelated')
            self.end_headers()

    own = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
    for server in (own, other):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert bc.http_get(f'http://127.0.0.1:{own.server_port}/api') == 302
        assert not hits
    finally:
        for server in (own, other):
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize('url', ['http://example.com/api', 'http://localhost:3000/api',
                                'http://127.0.0.1:3000/../secret', 'http://127.0.0.1:3000//other',
                                'http://user:password@127.0.0.1:3000/api'])
def test_only_literal_loopback_origin_is_accepted(monkeypatch, url):
    monkeypatch.setattr(bc.http.client, 'HTTPConnection', lambda *a, **k: pytest.fail('unexpected host request'))
    assert bc.http_get(url) == 0


def test_slow_drip_headers_have_a_wall_clock_deadline():
    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                for char in b'HTTP/1.0 200 OK\r\nX-Padding: ' + b'x' * 100:
                    self.connection.sendall(bytes([char]))
                    time.sleep(0.01)
            except OSError:
                pass
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        started = time.monotonic()
        assert bc.http_get(f'http://127.0.0.1:{server.server_port}/', timeout=0.1) == 0
        assert time.monotonic() - started < 0.7
    finally:
        server.shutdown()
        server.server_close()


def test_probe_preserves_queries_encodes_unicode_and_does_not_read_the_body(monkeypatch):
    seen = []
    class Connection:
        sock = NS(shutdown=lambda *a: None, close=lambda: None)
        def __init__(self, *a, **k):
            pass
        def connect(self):
            pass
        def request(self, method, target, **kwargs):
            seen.append((method, target))
        def getresponse(self):
            return NS(status=200, read=lambda *a: pytest.fail('response body read'))
        def close(self):
            pass
    monkeypatch.setattr(bc.http.client, 'HTTPConnection', Connection)
    assert bc.http_get('http://127.0.0.1:3000/api/caf\u00e9?q=\u00e9') == 200
    assert seen == [('GET', '/api/caf%C3%A9?q=%C3%A9')]


def test_an_unattributed_connected_peer_receives_no_http_request(monkeypatch):
    class Connection:
        sock = NS(shutdown=lambda *a: None, close=lambda: None)
        def __init__(self, *a, **k):
            pass
        def connect(self):
            pass
        def request(self, *a, **k):
            pytest.fail('HTTP request sent before peer attribution')
        def close(self):
            pass
    monkeypatch.setattr(bc.http.client, 'HTTPConnection', Connection)
    with pytest.raises(bc.ProbeUnavailable):
        bc.http_get('http://127.0.0.1:3000/api', validate=lambda sock: False)


@pytest.mark.parametrize('call', [
    "fetch('/api/save', requestOptions)",
    "fetch('/api/save', {method: 'POST', body: '" + 'x' * 300 + "'})",
    "fetch('/api/save', {method: method})", "fetch('/api/save', {...options})",
    "axios.get('/api/' + identifier)", "xhr.open('GET', '/api/' + identifier)",
    "fetch('//other/api')", "fetch('/%2e%2e/private')",
])
def test_unknown_requests_and_computed_paths_are_not_guessed(tmp_path, call):
    page = tmp_path / 'index.html'
    page.write_text('<script>' + call + '</script>', encoding='utf-8')
    assert bc.find_endpoints(tmp_path, page) == []


def test_comments_strings_and_html_text_are_not_requests(tmp_path):
    page = tmp_path / 'index.html'
    page.write_text('''<p>fetch('/api/html')</p><script>
// fetch('/api/comment')
/* fetch('/api/block') */
const example = "fetch('/api/string')";
fetch('/api/real?q=1');
</script>''', encoding='utf-8')
    assert bc.find_endpoints(tmp_path, page) == ['/api/real?q=1']


def test_discovery_and_script_reads_are_scoped_to_an_explicit_boundary(tmp_path):
    workspace = tmp_path / 'project'
    workspace.mkdir()
    (tmp_path / 'outside.js').write_text("require('http').createServer(() => {}).listen(3456)", encoding='utf-8')
    (workspace / 'package.json').write_text(json.dumps({'scripts': {'start': 'node ../outside.js'}}), encoding='utf-8')
    assert bc._entry_in(workspace) is None
    (tmp_path / 'server.js').write_text("require('http').createServer(() => {}).listen(3456)", encoding='utf-8')
    assert bc.find_entry(workspace) is None
    (tmp_path / 'private.js').write_text("fetch('/api/private')", encoding='utf-8')
    page = workspace / 'index.html'
    page.write_text('<script src="../private.js"></script>', encoding='utf-8')
    assert bc.find_endpoints(workspace, page) == []
    public = workspace / 'public'
    public.mkdir()
    (workspace / 'server.js').write_text("require('http').createServer(() => {}).listen(3456)", encoding='utf-8')
    assert bc.find_entry(public, boundary=workspace).cwd == workspace


def test_sensitive_source_is_never_opened(tmp_path):
    page = tmp_path / 'index.html'
    page.write_text('<script src="private.js"></script>', encoding='utf-8')
    (tmp_path / 'private.js').write_text("fetch('/api/private')", encoding='utf-8')
    assert bc.find_endpoints(tmp_path, page, deny=lambda path: path.endswith('private.js')) == []


def test_stale_and_unrelated_passes_never_suppress_page_service_caveats():
    state = TrajectoryState({}, goal='Create a web app', message_count=1)
    state.data['mutation_count'] = 1
    state.note_backend_asked('other-project/index.html')
    state.record_backend_check('first-project', bc.PASSED, 'startup only; no endpoints discovered')
    assert state.backend_note()
    state.data['mutation_count'] = 2
    assert state.backend_check_status() == bc.UNAVAILABLE
    assert 'project changed' in state.backend_check_line()
    assert state.backend_note()
    state.data['verification'] = 'pending'
    later = TrajectoryState(state.data, goal='Summarize the changes', message_count=2)
    assert later.backend_check_status() == bc.OFF


def test_an_unowned_listener_is_never_probed(tmp_path):
    (tmp_path / 'server.py').write_text('import http.server', encoding='utf-8')
    now = [0]
    def clock():
        now[0] += 1
        return now[0]
    stopped = []
    proc = NS(poll=lambda: None)
    report = bc.run_backend_check(tmp_path, None, lambda *a: proc, startup=2,
                                  in_use=lambda port: False, owned=lambda *a: False,
                                  probe=lambda *a: pytest.fail('unowned listener was contacted'),
                                  stop=stopped.append, sleep=lambda *a: None, clock=clock)
    assert report.status == bc.UNAVAILABLE and stopped == [proc]


def test_an_exhausted_request_budget_never_launches_a_server(tmp_path):
    (tmp_path / 'server.py').write_text('import http.server', encoding='utf-8')
    report = bc.run_backend_check(tmp_path, None, lambda *a: pytest.fail('budget exhausted'), cancelled=lambda: True)
    assert report.status == bc.UNAVAILABLE and 'budget' in report.detail


def test_production_logs_are_bounded_in_memory():
    proc = NS(stdout=io.BytesIO(b'x' * 100_000 + b'RuntimeError: bad code'))
    bc.capture_output(proc)
    proc.backend_reader.join(timeout=2)
    assert len(proc.backend_output) <= 4000
    assert 'RuntimeError: bad code' in bc._output_of(proc)


def test_native_launcher_uses_structured_argv_and_a_scrubbed_environment(engine, tmp_path, monkeypatch):
    script = tmp_path / 'server.py'
    script.write_text('import http.server', encoding='utf-8')
    monkeypatch.setattr(engine.sys, 'platform', 'win32')
    monkeypatch.setattr(engine, '_resolve_sandbox_backend', lambda: 'native')
    monkeypatch.setattr(engine, '_native_sandbox_argv', lambda: ['node', 'runner.js'])
    monkeypatch.setattr(engine, '_native_sandbox_ready', lambda *a, **k: True)
    monkeypatch.setattr(engine, '_native_sandbox_node_toolchain', lambda **k: (None, None, None))
    monkeypatch.setattr(engine, '_agent_data_dir', lambda: tmp_path.parent / 'runtime-home')
    monkeypatch.setattr(engine, '_path_is_allowed', lambda path: Path(path).resolve().is_relative_to(tmp_path))
    monkeypatch.setenv('CUSTOM_API_KEY', 'test-only-value')
    monkeypatch.setenv('NODE_OPTIONS', '--require not-a-real-module')
    captured = {}
    def spawn(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return NS(stdout=io.BytesIO(b''))
    monkeypatch.setattr(engine.subprocess, 'Popen', spawn)
    engine._backend_server_launcher()(bc.Entry('python', script, tmp_path), 3456, tmp_path)
    assert captured['argv'][captured['argv'].index('--mode') + 1] == 'read-only'
    assert '-I' in captured['argv'] and '-c' in captured['argv']
    assert 'shell' not in captured
    assert 'CUSTOM_API_KEY' not in captured['env'] and 'NODE_OPTIONS' not in captured['env']
