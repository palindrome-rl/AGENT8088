"""run_with_service: start a server, run one check against it, stop it -- in one sandboxed command.

Where a service cannot be reached from this computer (no bridge or relay, a network the host cannot see), or for a
quick "start it and check it", the server and its check run in the same sandboxed command, so the check reaches the
server wherever it runs. The wrapper is run for real here (without a sandbox, which is the engine's part); the engine
side is tested with the sandbox command faked.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER = """import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): print('request', self.path, flush=True)
    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404); self.end_headers(); self.wfile.write(b'ok')
http.server.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
"""
PROBE = """import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen(f'http://127.0.0.1:{sys.argv[1]}{sys.argv[2]}', timeout=5).status)
except urllib.error.HTTPError as e:
    print('HTTP', e.code); sys.exit(1)
"""
PARENT = """import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])
open('child.pid', 'w').write(str(child.pid))
import runpy; sys.argv = ['server.py', sys.argv[1]]; runpy.run_path('server.py', run_name='__main__')
"""


@pytest.fixture
def wrapper(engine, tmp_path):
    (tmp_path / "server.py").write_text(SERVER, encoding="utf-8")
    (tmp_path / "probe.py").write_text(PROBE, encoding="utf-8")
    (tmp_path / "crash.py").write_text("raise SystemExit('config missing')\n", encoding="utf-8")
    (tmp_path / "quiet.py").write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    (tmp_path / "parent.py").write_text(PARENT, encoding="utf-8")
    (tmp_path / "wrap.py").write_text(engine._RUN_WITH_SERVICE_CODE, encoding="utf-8")
    from agent8088 import services
    py = f'"{sys.executable}"'

    def run(service, check, ready=20, check_timeout=30):
        port = services.free_port()
        spec = {"service": service.replace("{port}", str(port)), "check": check.replace("{port}", str(port)),
                "port": port, "log": "svc.log", "ready": ready, "check_timeout": check_timeout}
        (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        done = subprocess.run([sys.executable, "wrap.py", "spec.json"], cwd=tmp_path, capture_output=True, text=True,
                              timeout=ready + check_timeout + 30)
        return done.returncode, done.stdout, port
    return run, py, tmp_path


def test_a_check_that_passes(wrapper):
    run, py, _ = wrapper
    code, out, port = run(f"{py} server.py {{port}}", f"{py} probe.py {{port}} /health")
    assert code == 0 and f"answered on port {port}" in out and "[check: exit 0]" in out and "200" in out
    assert "request /health" in out   # the server's own output comes back too


def test_a_check_that_fails_gives_its_exit_status(wrapper):
    run, py, _ = wrapper
    code, out, _ = run(f"{py} server.py {{port}}", f"{py} probe.py {{port}} /missing")
    assert code == 1 and "[check: exit 1]" in out and "HTTP 404" in out


def test_a_server_that_crashes_is_reported_with_its_words(wrapper):
    run, py, _ = wrapper
    code, out, _ = run(f"{py} crash.py", f"{py} probe.py {{port}} /health")
    assert code == 3 and "exited with status 1 before it answered" in out and "config missing" in out
    assert "[check:" not in out


def test_a_server_that_never_listens_is_not_waited_for_forever(wrapper):
    run, py, _ = wrapper
    start = time.time()
    code, out, port = run(f"{py} quiet.py", "echo never", ready=2)
    assert code == 3 and f"did not answer on port {port} within 2 s" in out and time.time() - start < 20


def test_a_check_that_hangs_is_cut_off(wrapper):
    run, py, _ = wrapper
    code, out, _ = run(f"{py} server.py {{port}}", f"{py} quiet.py", check_timeout=2)
    assert code == 124 and "timed out after 2 s" in out


def test_the_server_and_what_it_started_are_stopped(wrapper):
    import psutil
    run, py, folder = wrapper
    code, _out, _ = run(f"{py} parent.py {{port}}", f"{py} probe.py {{port}} /health")
    assert code == 0
    child = int((folder / "child.pid").read_text())
    for _ in range(20):
        if not psutil.pid_exists(child):
            break
        time.sleep(0.25)
    assert not psutil.pid_exists(child)


# --- the engine ---------------------------------------------------------------------------------------------------


@pytest.fixture
def tool(engine, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    monkeypatch.setattr(engine, "_shell_workspace_root", tmp_path)

    def fake(command, timeout=25, python_code=False, image=""):
        name = command.split()[-1]
        seen.update(command=command, timeout=timeout,
                    spec=json.loads((tmp_path / name).read_text(encoding="utf-8")),
                    wrapper=(tmp_path / command.split()[-2]).read_text(encoding="utf-8"))
        return "[service started; it answered on port 1 after 0.1 s]\n[check: exit 0]\n3 passed in 0.1s"
    monkeypatch.setattr(engine, "_run_sandbox_command", fake)
    return engine, seen, tmp_path


def test_the_port_is_filled_in_and_the_files_are_removed(tool):
    engine, seen, folder = tool
    out = engine._exec_run_with_service({"service": "node app.js {port}", "check": "curl http://127.0.0.1:{port}/"})
    spec = seen["spec"]
    assert spec["service"] == f"node app.js {spec['port']}" and spec["check"].endswith(f":{spec['port']}/")
    assert seen["wrapper"] == engine._RUN_WITH_SERVICE_CODE
    assert not list(folder.glob(".agent8088-run-with-service-*"))
    assert "3 passed" in engine._unwrap_untrusted(out) and out.lstrip().startswith("<<<EXTERNAL_UNTRUSTED_CONTENT")


def test_the_files_are_removed_even_when_the_run_fails(tool, monkeypatch):
    engine, _, folder = tool
    monkeypatch.setattr(engine, "_run_sandbox_command", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        engine._exec_run_with_service({"service": "a", "check": "b"})
    assert not list(folder.glob(".agent8088-run-with-service-*"))


@pytest.mark.parametrize("args", [{"service": "", "check": "x"}, {"service": "x", "check": ""}, {}])
def test_both_commands_are_needed(engine, args):
    assert engine._exec_run_with_service(args).startswith("Error: run_with_service needs")


def test_no_sandbox_no_run(engine, monkeypatch):
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "unavailable")
    assert "sandbox" in engine._exec_run_with_service({"service": "a", "check": "b"}).lower()


def test_the_check_time_is_bounded(tool):
    engine, seen, _ = tool
    engine._exec_run_with_service({"service": "a", "check": "b", "timeout": 100000})
    assert seen["spec"]["check_timeout"] == 280


@pytest.mark.parametrize("service, check", [("rm -rf /", "echo ok"), ("python app.py", "rm -rf /")])
def test_both_commands_meet_the_hard_blocks(engine, monkeypatch, service, check):
    monkeypatch.setattr(engine, "_exec_run_with_service", lambda args: pytest.fail("must not run"))
    out = engine.run_tool("run_with_service", {"service": service, "check": check})
    assert "must not run" not in out


@pytest.mark.parametrize("check, result, status, want", [
    ("python -m pytest -q", "3 passed in 0.1s", "passed", "passed"),
    ("python -m pytest -q", "1 failed in 0.1s", "failed", "failed"),
    ("curl -fsS http://127.0.0.1:1/", "ok\nCommand exited with status 22.", "failed", "failed"),
    ("curl -fsS http://127.0.0.1:1/", "all tests passed", "passed", "unknown"),
])
def test_the_checks_result_is_evidence_like_a_command(engine, check, result, status, want):
    source = "exit status" if "exited with status" in result else "output"
    assert engine._evidence_status("run_with_service", {"check": check}, result, status, source) == want


def test_it_is_a_shell_tool(engine):
    spec = engine.TOOL_SPECS["run_with_service"]
    assert spec["mode"] == "shell" and "{service}" in spec["command"] and "{check}" in spec["command"]
