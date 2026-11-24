"""Services: long-lived processes started in the sandbox, reached from this computer, stopped with the session.

A shell command ends with the command that started it, so a server started by one command was gone before the next
one could test it. service_start runs a command in the sandbox the way execute_shell would, keeps it running, waits
until it answers on its port, and gives the address. These tests drive services.py directly with a fake launcher,
and the engine's tools with fake sandbox launchers; the live checks are in the PR.
"""
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent8088 import services


class _Listener:
    """Stands in for a service process: listens on the port until stopped."""

    def __init__(self, port, start_listening=True, die=False):
        self.port, self.stopped, self.die = port, False, die
        self.sock = None
        if start_listening:
            self.sock = socket.create_server(("127.0.0.1", port))
            threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stopped:
            try:
                conn, _ = self.sock.accept()
                conn.close()
            except OSError:
                return

    def stop(self):
        self.stopped = True
        if self.sock:
            self.sock.close()

    def alive(self):
        return not self.stopped and not self.die


@pytest.fixture
def registry(tmp_path):
    return services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=4242)


def _launch(made, **kw):
    def launch(command, port, log_path):
        log_path.write_text(f"started {command} on {port}\n", encoding="utf-8")
        listener = _Listener(port, **kw)
        made.append(listener)
        return services.Handle(stop=listener.stop, alive=listener.alive, host_port=port, pid=None)
    return launch


def test_a_service_starts_answers_and_stops(registry):
    made = []
    service = registry.start("web", "serve", "main", "native", _launch(made))
    assert services.listening(service.handle.host_port)
    assert service.url == f"http://127.0.0.1:{service.port}/"
    assert registry.status("main")[0]["running"] is True
    assert "started serve" in services.tail(service.log_path)
    assert registry.stop("web", "main") is service and made[0].stopped
    assert registry.status() == []


@pytest.mark.parametrize("name", ["", "Web Server", "../x", "a" * 33])
def test_a_bad_name_is_refused(registry, name):
    with pytest.raises(ValueError, match="name a service"):
        registry.start(name, "serve", "main", "native", _launch([]))


def test_names_are_unique_and_the_count_is_capped(registry, monkeypatch):
    monkeypatch.setattr(services, "MAX_SERVICES", 2)
    made = []
    registry.start("a", "x", "main", "native", _launch(made))
    with pytest.raises(ValueError, match="already running"):
        registry.start("a", "x", "main", "native", _launch(made))
    registry.start("b", "x", "main", "native", _launch(made))
    with pytest.raises(ValueError, match="2 services are already running"):
        registry.start("c", "x", "main", "native", _launch(made))


def test_a_busy_port_is_never_reused(registry):
    busy = socket.create_server(("127.0.0.1", 0))
    try:
        with pytest.raises(ValueError, match="already listens on port"):
            registry.start("web", "x", "main", "native", _launch([]), port=busy.getsockname()[1])
    finally:
        busy.close()


def test_an_owner_sees_and_stops_only_its_own(registry):
    registry.start("web", "x", "main", "native", _launch([]))
    registry.start("job", "x", "child-1", "native", _launch([]))
    assert [s["name"] for s in registry.status("child-1")] == ["job"]
    assert registry.stop("web", "child-1") is None and registry.get("web") is not None
    assert registry.stop_all("child-1") == ["job"] and [s["name"] for s in registry.status()] == ["web"]


def test_a_service_that_never_listens_or_dies_is_reported_not_waited_for(registry):
    start = time.monotonic()
    service = registry.start("web", "x", "main", "native", _launch([], start_listening=False, die=True),
                             ready_seconds=10)
    assert time.monotonic() - start < 3 and not services.listening(service.handle.host_port)


def test_only_running_services_open_host_ports(registry):
    a = registry.start("a", "x", "main", "native", _launch([]))
    assert registry.host_ports() == {a.handle.host_port}
    registry.stop("a")
    assert registry.host_ports() == set()


def test_a_crashed_session_is_cleaned_up_by_the_next(tmp_path):
    first = services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=111)
    first.start("web", "x", "main", "docker", lambda c, p, l: services.Handle(
        stop=lambda: None, alive=lambda: True, host_port=p, pid=999, containers=["agent8088-111-abc"]))
    killed, removed = [], []
    second = services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=222)
    assert second.reap_stale(lambda pid: pid != 111, killed.append, removed.extend) == ["web"]
    assert killed == [999] and removed == ["agent8088-111-abc"]
    assert second.reap_stale(lambda pid: False, killed.append, removed.extend) == []   # recorded once


def test_a_running_session_is_left_alone(tmp_path):
    first = services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=111)
    first.start("web", "x", "main", "native", _launch([]))
    second = services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=222)
    assert second.reap_stale(lambda pid: True, lambda pid: None, lambda names: None) == []


# --- the engine's tools -------------------------------------------------------------------------------------


@pytest.fixture
def tools(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_services_registry",
                        services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=4242))
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    monkeypatch.setattr(engine, "_shell_workspace_root", tmp_path)
    made = []
    monkeypatch.setattr(engine, "_service_launcher", lambda backend, cwd, name: _launch(made))
    return engine, made


def test_the_tools_start_list_log_and_stop(tools):
    engine, made = tools
    out = engine._exec_service("service_start", {"name": "web", "command": "serve {port}"})
    assert out.startswith("Service web is running and answering at http://127.0.0.1:")
    assert "web: http://127.0.0.1:" in engine._exec_service("service_status", {})
    assert "started serve" in engine._exec_service("service_logs", {"name": "web"})
    assert engine._exec_service("service_stop", {"name": "web"}) == "Stopped web." and made[0].stopped
    assert engine._exec_service("service_status", {}) == "No services are running."
    assert engine._exec_service("service_stop", {"name": "web"}).startswith("Error: no service named")


def test_a_service_that_exits_is_reported_with_its_output(tools, monkeypatch):
    engine, _ = tools
    monkeypatch.setattr(engine, "_service_launcher", lambda b, c, n: _launch([], start_listening=False, die=True))
    out = engine._exec_service("service_start", {"name": "web", "command": "crash"})
    assert out.startswith("Service web exited before it answered") and "started crash" in out
    assert engine._services().get("web") is None


def test_no_sandbox_no_service(tools, monkeypatch):
    engine, _ = tools
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "unavailable")
    assert "sandbox" in engine._exec_service("service_start", {"name": "web", "command": "x"}).lower()


def test_the_tools_are_shell_tools_so_every_shell_check_applies(engine):
    for name in ("service_start", "service_status", "service_logs", "service_stop"):
        assert engine.TOOL_SPECS[name]["mode"] == "shell"


def test_a_service_command_still_meets_the_hard_blocks(engine, monkeypatch):
    called = []
    monkeypatch.setattr(engine, "_exec_service", lambda name, args: called.append(name) or "ran")
    out = engine.run_tool("service_start", {"name": "x", "command": "rm -rf /"})
    assert not called and "ran" not in out


# --- the launchers' commands ----------------------------------------------------------------------------------


def test_the_native_launcher_gives_the_port_and_starts_in_the_background(engine, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda cwd: True)
    monkeypatch.setattr(engine, "_exec_native_sandbox",
                        lambda command, timeout, cwd=None: seen.update(command=command)
                        or engine._spawn_service(["runner", command]))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(engine, "_service_popen", lambda argv, log: seen.update(argv=argv) or proc)
    handle = engine._service_launcher("native", tmp_path, "web")("serve --port {port}", 5555, tmp_path / "l.log")
    assert seen["command"] == 'set "PORT=5555" && serve --port 5555' and handle.host_port == 5555
    assert handle.alive() and engine._spawn_service is None
    handle.stop()
    proc.wait(timeout=10)
    assert not handle.alive()


def test_the_linux_launcher_bridges_the_port(engine, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda cwd: True)
    monkeypatch.setattr(engine, "_exec_native_sandbox",
                        lambda command, timeout, cwd=None: seen.update(command=command)
                        or engine._spawn_service(["srt", command]))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(engine, "_service_popen", lambda argv, log: proc)
    monkeypatch.setattr(engine, "_UnixBridge", lambda path, port: type("B", (), {"close": lambda self: None, "ready": lambda self: True})())
    handle = engine._service_launcher("native", tmp_path, "web")("serve", 5556, tmp_path / "l.log")
    assert "socat UNIX-LISTEN:" in seen["command"] and "TCP:127.0.0.1:5556" in seen["command"]
    assert handle.host_port != 5556 and "unix socket" in handle.note
    handle.stop()


def test_linux_service_settings_allow_the_bridge_and_hide_host_sockets(engine, tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    monkeypatch.setattr(engine.os.path, "exists", lambda p: p in ("/var/run/docker.sock", "/run/user"))
    data = json.loads(engine._write_service_settings(tmp_path).read_text(encoding="utf-8"))
    assert data["network"]["allowAllUnixSockets"] is True
    assert {"/var/run/docker.sock", "/run/user"} <= set(data["filesystem"]["denyRead"])
    assert {"/var/run/docker.sock", "/run/user"} <= set(data["filesystem"]["denyWrite"])


def test_a_normal_command_never_gets_unix_sockets(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    assert "allowAllUnixSockets" not in engine._sandbox_settings_data(False, tmp_path)["network"]
