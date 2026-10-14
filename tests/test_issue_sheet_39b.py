"""Issue sheet 39b: a script that serves must actually be booted once.

The sandbox runner is stubbed the way tests/test_app_check.py stubs it, except
where termination matters: there the stub is a local runner that executes the
probe command for real, with the same kill discipline the sandbox runner has
(its own process group; a timeout kills the whole tree). No Docker, no real
sandbox -- and every test names what must be true when no process survives.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest

from agent8088 import app_check


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _accepts(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def _kill_tree(process):
    """The sandbox runner's discipline: a timeout kills the tree, not the shell."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _local_runner(cwd):
    """A sandbox stand-in that runs the probe command for real, mirroring
    _exec_process: stdin closed, its own process group, and a timeout that
    kills the whole tree and reports "Command timed out after Ns."."""
    def run(command, timeout):
        kwargs = {"shell": True, "stdin": subprocess.DEVNULL,
                  "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                  "cwd": str(cwd)}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
            kwargs["executable"] = shutil.which("bash") or "/bin/sh"
        with subprocess.Popen(command, **kwargs) as process:
            try:
                out, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(process)
                out, _ = process.communicate(timeout=10)
                tail = (out or b"").decode(errors="replace").strip()
                return f"Command timed out after {timeout}s. Last output:\n{tail}"
        text = (out or b"").decode(errors="replace").strip()
        if process.returncode:
            return f"{text}\nCommand exited with status {process.returncode}.".strip()
        return text or "Command completed."
    return run


SERVER = """import http.server
PORT = {port}
handler = http.server.SimpleHTTPRequestHandler
server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
print("serving", flush=True)
server.serve_forever()
"""

# Declares one port but binds another, so the probe must wait out the window,
# report the declared port as refused -- and still stop the process it started.
NEVER_SERVES = """import socket, time
PORT = {declared}
keeper = socket.socket()
keeper.bind(("127.0.0.1", {actual}))
keeper.listen(1)
time.sleep(30)
"""

DIES_AT_BOOT = """import sys
PORT = {port}
handler = None
if handler:
    handler.serve_forever()
raise RuntimeError("the port is already taken")
"""

NODE_SERVER = """const http = require('http');
const PORT = {port};
http.createServer((req, res) => res.end('hi')).listen(PORT);
"""


def _script(tmp_path, text, name="server.py"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- the probe verifies a real boot ------------------------------------------------------

def test_a_real_server_boots_serves_and_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "BOOT_WAIT", 6.0)
    port = _free_port()
    script = _script(tmp_path, SERVER.format(port=port))
    report = app_check.check_script(script, run=_local_runner(tmp_path))
    assert report.status == app_check.PASS, report.text()
    assert f"127.0.0.1:{port}" in report.note
    time.sleep(0.3)
    assert not _accepts(port), "the probe must stop the server it started"


def test_a_server_that_never_serves_fails_and_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "BOOT_WAIT", 1.5)
    declared, actual = _free_port(), _free_port()
    script = _script(tmp_path, NEVER_SERVES.format(declared=declared, actual=actual))
    report = app_check.check_script(script, run=_local_runner(tmp_path))
    assert report.failed, report.text()
    assert str(declared) in report.failures[0]
    time.sleep(0.3)
    assert not _accepts(actual), "a refused server must still be stopped"


def test_a_server_that_dies_at_boot_fails_with_its_last_words(tmp_path):
    port = _free_port()
    script = _script(tmp_path, DIES_AT_BOOT.format(port=port))
    report = app_check.check_script(script, run=_local_runner(tmp_path))
    assert report.failed, report.text()
    assert "exited before accepting connections" in report.failures[0]
    assert "already taken" in report.failures[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_node_server_boots_and_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "BOOT_WAIT", 6.0)
    port = _free_port()
    script = _script(tmp_path, NODE_SERVER.format(port=port), name="server.js")
    report = app_check.check_script(script, run=_local_runner(tmp_path))
    assert report.status == app_check.PASS, report.text()
    time.sleep(0.3)
    assert not _accepts(port), "the probe must stop the server it started"


# --- nothing may leak: timeout, exception, cancellation ------------------------------------

def test_a_run_timeout_kills_the_whole_probe_tree(tmp_path, monkeypatch):
    # The poll window outlasts the runner's clock, so only the runner's
    # tree-kill can end the probe -- exactly what a real timeout relies on.
    monkeypatch.setattr(app_check, "BOOT_WAIT", 30.0)
    monkeypatch.setattr(app_check, "BOOT_RUN_TIMEOUT", 3)
    declared, actual = _free_port(), _free_port()
    script = _script(tmp_path, NEVER_SERVES.format(declared=declared, actual=actual))
    report = app_check.check_script(script, run=_local_runner(tmp_path))
    assert report.status == app_check.UNAVAILABLE
    assert "did not finish" in report.note
    time.sleep(0.3)
    assert not _accepts(actual), "a timeout must not leave the server running"


def test_a_broken_runner_cannot_leave_the_server_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "BOOT_WAIT", 1.5)
    real = _local_runner(tmp_path)
    declared, actual = _free_port(), _free_port()
    script = _script(tmp_path, NEVER_SERVES.format(declared=declared, actual=actual))

    def exploding(command, timeout):
        real(command, timeout)     # the probe command runs to its end, cleaning up
        raise RuntimeError("runner exploded")

    report = app_check.check_script(script, run=exploding)
    assert report.status == app_check.UNAVAILABLE
    assert "could not run" in report.note
    time.sleep(0.3)
    assert not _accepts(actual), "the stop lives inside the command, so an exception on this side leaks nothing"


def test_cancellation_propagates_but_the_server_is_already_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "BOOT_WAIT", 1.5)
    real = _local_runner(tmp_path)
    declared, actual = _free_port(), _free_port()
    script = _script(tmp_path, NEVER_SERVES.format(declared=declared, actual=actual))

    def cancelled(command, timeout):
        real(command, timeout)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        app_check.check_script(script, run=cancelled)
    time.sleep(0.3)
    assert not _accepts(actual), "cancellation must propagate, not leak the server"


# --- a non-server is never started ---------------------------------------------------------

def test_a_plain_utility_is_never_started_in_the_background(tmp_path):
    script = _script(tmp_path, "print(open('data.txt').read())\n", name="tool.py")
    commands = []

    def run(command, timeout):
        commands.append(command)
        return "usage: tool.py [-h]"

    report = app_check.check_script(script, run=run)
    assert report.status == app_check.PASS, report.text()
    assert len(commands) == 1 and "--help" in commands[0], "only the cheap check may run"


def test_a_server_goes_straight_to_the_boot_probe_not_help(tmp_path):
    script = _script(tmp_path, "from flask import Flask\napp = Flask(__name__)\napp.run(port=5000)\n")
    commands = []

    def run(command, timeout):
        commands.append((command, timeout))
        return "BOOT:SERVING"

    report = app_check.check_script(script, run=run)
    assert report.status == app_check.PASS
    assert "127.0.0.1:5000" in report.note
    assert len(commands) == 1 and "--help" not in commands[0][0]
    assert commands[0][1] == app_check.BOOT_RUN_TIMEOUT


def test_a_server_whose_port_is_unknown_is_reported_not_guessed(tmp_path):
    script = _script(tmp_path, "import os\nfrom flask import Flask\n"
                               "app = Flask(__name__)\n"
                               "app.run(port=int(os.environ.get('PORT', '0')))\n")
    commands = []

    def run(command, timeout):
        commands.append(command)
        return "usage: app.py [-h]"

    report = app_check.check_script(script, run=run)
    assert report.status == app_check.UNAVAILABLE
    assert "could not be determined" in report.note
    assert len(commands) == 1 and "--help" in commands[0], "no port, no boot attempt"


# --- verdicts and vocabulary ----------------------------------------------------------------

def test_port_open_and_port_refused_are_distinguished(tmp_path):
    script = _script(tmp_path, "import http.server\nPORT = 8000\n"
                               "http.server.HTTPServer(('', PORT), None).serve_forever()\n")
    opened = app_check.check_script(script, run=lambda c, t: "BOOT:SERVING")
    assert opened.status == app_check.PASS and "accepts connections" in opened.note
    refused = app_check.check_script(script, run=lambda c, t: "BOOT:REFUSED")
    assert refused.failed and "never accepted a connection" in refused.failures[0]
    assert not opened.failed


def test_boot_verdicts_map_to_readable_reports(tmp_path):
    script = _script(tmp_path, "import http.server\nPORT = 8000\n"
                               "http.server.HTTPServer(('', PORT), None).serve_forever()\n")
    died = app_check.check_script(script, run=lambda c, t:
                                  "BOOT:DIED: RuntimeError: config missing\nCommand exited with status 1.")
    assert died.failed and "exited before accepting" in died.failures[0]
    assert "config missing" in died.failures[0]
    not_started = app_check.check_script(script, run=lambda c, t: "BOOT:NOTSTARTED [Errno 2] no such file")
    assert not_started.status == app_check.UNAVAILABLE and "could not start" in not_started.note
    silent = app_check.check_script(script, run=lambda c, t: "Error: Docker sandbox is not available.")
    assert silent.status == app_check.UNAVAILABLE and "not verified" in silent.note
    hung = app_check.check_script(script, run=lambda c, t: "Command timed out after 15s.")
    assert hung.status == app_check.UNAVAILABLE and "did not finish" in hung.note


def test_server_detection_is_conservative():
    assert app_check.server_port("from flask import Flask\napp.run(port=5000)\n") == 5000
    assert app_check.server_port(
        "const http = require('http');\nhttp.createServer(() => {}).listen(3000);\n") == 3000
    assert app_check.server_port(
        "import http.server\nhttp.server.ThreadingHTTPServer(('', 8000), None).serve_forever()\n") == 8000
    # A --port flag alone is not a listener: a client utility can carry one.
    assert app_check.server_port(
        "import argparse\nparser.add_argument('--port', type=int, default=8080)\n") is None
    assert app_check.server_port("print(open('a.txt').read())\n") is None
    # Flask's default 5000 is a guess the report refuses to make.
    assert app_check.server_port("from flask import Flask\napp.run()\n") is None
    assert app_check.looks_like_server(
        "import argparse\nparser.add_argument('--port', type=int)\n")
    assert not app_check.looks_like_server("x = 1\nprint('serve.py is just a name')\n")