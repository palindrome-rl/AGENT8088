"""The optional backend check: start the project's own server, probe the GET endpoints its page uses, stop it, and record the result
in a separate field (off, not applicable, unavailable, passed, failed) that never changes the verification level."""
import os
import socket
import subprocess
import sys
import tempfile

import pytest

from agent8088 import backend_check as bc
from agent8088.trajectory import TrajectoryState

SERVER = '''
import sys, json, http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body, code = b"<html></html>", 200
        elif self.path.startswith("/api/pokemon"):
            body, code = json.dumps({"ok": 1}).encode(), 200
        elif self.path.startswith("/api/boom"):
            body, code = b"boom", 500
        else:
            body, code = b"nope", 404
        self.send_response(code); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''
CRASH = "import http.server\nraise RuntimeError('config missing')\n"
MISSING_DEP = "import http.server\nimport flask_cors\n"
READONLY = "import http.server\nraise OSError(30, 'Read-only file system', 'notes.db')\n"
SILENT = "import http.server\nimport time\ntime.sleep(60)\n"
PAGE = "<html><body><script>fetch('/api/pokemon').then(r => r.json());</script><button>go</button></body></html>"


def host_launcher(entry, port, root):
    if sys.platform == 'darwin':
        pytest.skip('macOS peer PID attribution requires elevated privileges; production backend launcher is Windows-only')
    log = tempfile.NamedTemporaryFile(prefix="bc-test-", suffix=".log", delete=False)
    proc = subprocess.Popen([sys.executable, str(entry.script)] + ([str(port)] if entry.port_arg else []), cwd=str(entry.cwd),
                            stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PORT=str(port)))
    proc.output_file = log.name
    log.close()
    return proc


def project(tmp_path, server=SERVER, page=PAGE):
    (tmp_path / "server.py").write_text(server, encoding="utf-8")
    (tmp_path / "index.html").write_text(page, encoding="utf-8")
    return tmp_path, tmp_path / "index.html"


# -- finding the server and the endpoints ---------------------------------------------------------

def test_a_python_server_is_found_and_takes_the_port_as_an_argument(tmp_path):
    root, _ = project(tmp_path)
    entry = bc.find_entry(root)
    assert entry.kind == "python" and entry.script.name == "server.py" and entry.port_arg and entry.port is None


def test_npm_start_and_a_literal_port_are_found(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "index.js").write_text("const app = require('express')();\napp.listen(3456, () => {});\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node server/index.js"}}', encoding="utf-8")
    entry = bc.find_entry(tmp_path)
    assert entry.kind == "node" and entry.script.name == "index.js" and entry.port == 3456


def test_a_port_from_the_environment_is_not_a_literal(tmp_path):
    (tmp_path / "server.js").write_text("const PORT = process.env.PORT || 3000;\nrequire('http').createServer(() => {}).listen(PORT);\n", encoding="utf-8")
    assert bc.find_entry(tmp_path).port is None


def test_a_static_project_and_a_helper_script_have_no_server(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    assert bc.find_entry(tmp_path) is None


def test_endpoints_are_plain_gets_the_page_uses(tmp_path):
    (tmp_path / "app.js").write_text(
        "fetch('/api/a'); fetch(\"/api/b?x=1\"); axios.get('/api/c'); $.getJSON('/api/d');\n"
        "fetch('/api/save', {method: 'POST', body: '{}'}); fetch(`/api/${id}`); fetch('/img/x.png'); fetch('https://example.com/y');\n"
        "const x = new XMLHttpRequest(); x.open('GET', '/api/e');\n", encoding="utf-8")
    page = tmp_path / "index.html"
    page.write_text("<script src='app.js'></script><script>fetch('/api/inline')</script>", encoding="utf-8")
    assert set(bc.find_endpoints(tmp_path, page)) == {"/api/inline", "/api/a", "/api/b?x=1", "/api/c", "/api/d", "/api/e"}


def test_the_number_of_endpoints_is_bounded(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<script>" + "".join(f"fetch('/api/n{i}');" for i in range(30)) + "</script>", encoding="utf-8")
    assert len(bc.find_endpoints(tmp_path, page)) == bc.MAX_ENDPOINTS


# -- the check -----------------------------------------------------------------------------------

def test_a_working_server_passes_and_is_stopped(tmp_path):
    root, page = project(tmp_path)
    seen = {}

    def launcher(entry, port, root):
        seen["port"] = port
        seen["proc"] = host_launcher(entry, port, root)
        return seen["proc"]

    report = bc.run_backend_check(root, page, launcher, startup=15)
    assert report.status == bc.PASSED and report.endpoints == [{"path": "/api/pokemon", "status": 200}]
    assert seen["proc"].poll() is not None and not bc.port_in_use(seen["port"])


def test_an_endpoint_the_page_uses_that_the_server_lacks_fails(tmp_path):
    root, page = project(tmp_path, page="<script>fetch('/api/pokemon'); fetch('/api/missing'); fetch('/api/boom')</script>")
    report = bc.run_backend_check(root, page, host_launcher, startup=15)
    assert report.status == bc.FAILED
    assert "GET /api/missing -> 404" in report.detail and "GET /api/boom -> 500" in report.detail


def test_no_endpoints_still_checks_that_the_server_starts(tmp_path):
    root, page = project(tmp_path, page="<html><button>go</button></html>")
    report = bc.run_backend_check(root, page, host_launcher, startup=15)
    assert report.status == bc.PASSED and "names no GET endpoint" in report.detail


def test_a_server_that_dies_of_its_own_code_fails(tmp_path):
    root, page = project(tmp_path, server=CRASH)
    report = bc.run_backend_check(root, page, host_launcher, startup=15)
    assert report.status == bc.FAILED and "config missing" in report.detail


@pytest.mark.parametrize("server, word", [(MISSING_DEP, "flask_cors"), (READONLY, "Read-only file system")])
def test_an_environment_problem_is_unavailable_never_failed(tmp_path, server, word):
    root, page = project(tmp_path, server=server)
    report = bc.run_backend_check(root, page, host_launcher, startup=15)
    assert report.status == bc.UNAVAILABLE and word in report.detail


def test_a_dependency_that_is_a_file_of_the_project_is_the_projects_bug(tmp_path):
    (tmp_path / "helpers.py").write_text("x = 1\n", encoding="utf-8")
    status, _ = bc.classify_exit("ModuleNotFoundError: No module named 'helpers'", tmp_path)
    assert status == bc.FAILED


def test_a_server_that_never_answers_is_unavailable_and_stopped(tmp_path):
    root, page = project(tmp_path, server=SILENT)
    holder = {}

    def launcher(entry, port, root):
        holder["proc"] = host_launcher(entry, port, root)
        return holder["proc"]

    report = bc.run_backend_check(root, page, launcher, startup=2)
    assert report.status == bc.UNAVAILABLE and "did not answer" in report.detail
    assert holder["proc"].poll() is not None


def test_a_port_already_in_use_is_never_tested(tmp_path):
    (tmp_path / "server.js").write_text("require('http').createServer(() => {}).listen(4999);\n", encoding="utf-8")
    called = []
    report = bc.run_backend_check(tmp_path, None, lambda *a: called.append(a), in_use=lambda port: True)
    assert report.status == bc.UNAVAILABLE and "already in use" in report.detail and not called


def test_no_launcher_and_a_failing_launcher_are_unavailable(tmp_path):
    root, page = project(tmp_path)
    assert bc.run_backend_check(root, page, None).status == bc.UNAVAILABLE

    def broken(*args):
        raise RuntimeError("the native sandbox is not ready")

    report = bc.run_backend_check(root, page, broken)
    assert report.status == bc.UNAVAILABLE and "not ready" in report.detail


def test_no_server_is_not_applicable(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    assert bc.run_backend_check(tmp_path, tmp_path / "index.html", host_launcher).status == bc.NOT_APPLICABLE


def test_the_server_is_stopped_even_when_probing_raises(tmp_path):
    root, page = project(tmp_path)
    stopped = []

    def probe(url):
        if url.endswith("/api/pokemon"):
            raise RuntimeError("probe blew up")
        return 200

    with pytest.raises(RuntimeError):
        bc.run_backend_check(root, page, host_launcher, probe=probe, stop=lambda proc: (stopped.append(proc), bc.stop_process(proc)), startup=15)
    assert stopped and stopped[0].poll() is not None


# -- the field in the answer; off by default; never the level -------------------------------------

def _state(**data):
    state = TrajectoryState({}, goal="Create a web app", message_count=1)
    state.data.update({"mutation_count": 1, "ran_ok_at": 1, "written_paths": ["index.html"]})
    state.data.update(data)
    return state


def test_the_check_is_off_by_default(engine):
    assert engine.BACKEND_CHECK is False


def test_off_records_nothing_and_adds_no_line(engine, tmp_path, monkeypatch):
    root, page = project(tmp_path)
    monkeypatch.setattr(engine, "BACKEND_CHECK", False)
    state = _state()
    engine._backend_check_step(state, [page])
    assert state.backend_check_status() == "off" and state.backend_check_line() == ""
    assert "Backend check" not in engine._with_task_notes("Done.", state)


def test_on_records_the_result_without_changing_the_level(engine, tmp_path, monkeypatch):
    root, page = project(tmp_path)
    monkeypatch.setattr(engine, 'PROJECT_ROOT', root)
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [root])
    monkeypatch.setattr(engine, "BACKEND_CHECK", True)
    monkeypatch.setattr(engine, "BACKEND_CHECK_STARTUP", 15)
    monkeypatch.setattr(engine, "_backend_server_launcher", lambda: host_launcher)
    state = _state()
    level = state.assurance()
    engine._backend_check_step(state, [page])
    assert state.backend_check_status() == "passed"
    assert state.assurance() == level
    answer = engine._with_task_notes("Done.", state)
    assert "Backend check: passed" in answer
    engine._backend_check_step(state, [page])                      # once per state of the work
    assert state.backend_check_current(str(root))


def test_a_failed_check_is_a_line_and_not_a_failed_run(engine, tmp_path, monkeypatch):
    root, page = project(tmp_path, page="<script>fetch('/api/missing')</script>")
    monkeypatch.setattr(engine, 'PROJECT_ROOT', root)
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [root])
    monkeypatch.setattr(engine, "BACKEND_CHECK", True)
    monkeypatch.setattr(engine, "BACKEND_CHECK_STARTUP", 15)
    monkeypatch.setattr(engine, "_backend_server_launcher", lambda: host_launcher)
    state = _state()
    engine._backend_check_step(state, [page])
    assert state.backend_check_status() == "failed"
    assert state.assurance() == "exercised"
    assert "Backend check: failed" in engine._with_task_notes("Done.", state)


def test_get_status_pass_is_separate_from_page_integration_evidence(engine):
    state = _state()
    state.note_backend_asked("index.html")
    assert "Not verified" in engine._with_task_notes("Done.", state)
    state.record_backend_check("k", "unavailable", "port 3000 is already in use")
    answer = engine._with_task_notes("Done.", state)
    assert "Backend check: unavailable" in answer and "Not verified" in answer
    state.record_backend_check("k", "passed", "the server started")
    answer = engine._with_task_notes("Done.", state)
    assert "Backend check: passed" in answer and "Not verified" in answer


def test_a_problem_inside_the_check_never_ends_the_run(engine, tmp_path, monkeypatch):
    root, page = project(tmp_path)
    monkeypatch.setattr(engine, 'PROJECT_ROOT', root)
    monkeypatch.setattr(engine, 'ALLOWED_PATHS', [root])
    monkeypatch.setattr(engine, "BACKEND_CHECK", True)
    monkeypatch.setattr(engine.backend_check, "run_backend_check", lambda *a, **k: (_ for _ in ()).throw(ValueError("bug")))
    state = _state()
    engine._backend_check_step(state, [page])
    assert state.backend_check_status() == "unavailable" and "could not run" in state.backend_check_line()


def test_the_listening_socket_helper_sees_a_busy_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        assert bc.port_in_use(sock.getsockname()[1])
    assert not bc.port_in_use(bc.free_port())
