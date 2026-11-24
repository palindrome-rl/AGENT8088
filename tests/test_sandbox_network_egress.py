"""Sandbox network: a built-in allowlist to opt into, an allowlist proxy the
Docker fallback can use instead of no network at all, and status that says what
the Windows sandbox really enforces. No real Docker runs; the proxy runs on
localhost. The defaults stay closed."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent8088 import capabilities, egress_proxy


def test_no_setting_means_the_registry_preset_and_none_closes_it(engine):
    assert engine._sandbox_allowed_domains({}) == list(engine._DEFAULT_SANDBOX_ALLOWED_DOMAINS)
    assert engine._sandbox_allowed_domains({"sandbox_allowed_domains": "none"}) == []
    assert engine.DOCKER_NETWORK == "none"


def test_default_keyword_opts_into_the_builtin_list(engine):
    builtin = engine._sandbox_allowed_domains({"sandbox_allowed_domains": "default"})
    assert "pypi.org" in builtin and "registry.npmjs.org" in builtin
    assert engine._sandbox_allowed_domains({"sandbox_allowed_domains": "none"}) == []
    assert engine._sandbox_allowed_domains({"sandbox_allowed_domains": ""}) == []
    assert engine._sandbox_allowed_domains(
        {"sandbox_allowed_domains": "api.example.com"}) == ["api.example.com"]
    combined = engine._sandbox_allowed_domains(
        {"sandbox_allowed_domains": "default, api.example.com"})
    assert "pypi.org" in combined and combined[-1] == "api.example.com"


def test_proxy_host_matching():
    patterns = ["pypi.org", "*.github.com", "api.example.com:443"]
    assert egress_proxy.host_allowed("pypi.org", 443, patterns)
    assert egress_proxy.host_allowed("PyPI.org.", 443, patterns)
    assert not egress_proxy.host_allowed("evil-pypi.org", 443, patterns)
    assert egress_proxy.host_allowed("api.github.com", 443, patterns)
    assert not egress_proxy.host_allowed("github.com", 443, patterns)  # apex is separate
    assert egress_proxy.host_allowed("api.example.com", 443, patterns)
    assert not egress_proxy.host_allowed("api.example.com", 8080, patterns)


@pytest.fixture
def proxy():
    """The proxy's handler behind a localhost listener; returns (port, patterns)."""
    patterns = ["127.0.0.1"]
    server = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=egress_proxy.handle, args=(conn, patterns),
                             daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield server.getsockname()[1], patterns
    server.close()


def _ask(port, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(data)
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


def test_proxy_refuses_a_host_outside_the_list(proxy):
    port, _ = proxy
    reply = _ask(port, b"CONNECT example.org:443 HTTP/1.1\r\nHost: example.org:443\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 403")
    assert b"not in sandbox_allowed_domains" in reply


def test_proxy_forwards_plain_http_to_an_allowed_host(proxy):
    port, _ = proxy
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"] = self.path
            body = b"hello from upstream"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    upstream = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=upstream.handle_request, daemon=True).start()
    target = f"http://127.0.0.1:{upstream.server_address[1]}/simple/x?y=1"
    reply = _ask(port, f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                       "Proxy-Connection: keep-alive\r\n\r\n".encode())
    upstream.server_close()
    assert reply.startswith(b"HTTP/1.0 200") or reply.startswith(b"HTTP/1.1 200")
    assert reply.endswith(b"hello from upstream")
    assert seen["path"] == "/simple/x?y=1"  # origin-form, not the absolute URL


def test_proxy_tunnels_connect_to_an_allowed_host(proxy):
    port, _ = proxy
    echo = socket.create_server(("127.0.0.1", 0))

    def serve_once():
        conn, _ = echo.accept()
        with conn:
            conn.sendall(conn.recv(1024).upper())

    threading.Thread(target=serve_once, daemon=True).start()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(f"CONNECT 127.0.0.1:{echo.getsockname()[1]} HTTP/1.1\r\n\r\n".encode())
        assert conn.recv(1024).startswith(b"HTTP/1.1 200")
        conn.sendall(b"tunnelled")
        assert conn.recv(1024) == b"TUNNELLED"
    echo.close()


class _FakeDocker:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        verb = argv[1] if argv[1] != "network" else f"network {argv[2]}"
        failed = verb == self.fail_on or verb == "network inspect" or verb == "inspect"
        return type("Done", (), {"returncode": 1 if failed else 0, "stdout": "",
                                 "stderr": "boom" if failed else ""})()


@pytest.fixture
def allowlist_mode(engine, monkeypatch):
    monkeypatch.setattr(engine, "DOCKER_NETWORK", "allowlist")
    monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", ["pypi.org", "registry.npmjs.org"])
    return engine


def test_docker_egress_starts_internal_network_and_proxy(allowlist_mode, monkeypatch):
    engine = allowlist_mode
    fake = _FakeDocker()
    monkeypatch.setattr(engine.subprocess, "run", fake)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    network, args = engine._docker_egress_args()
    assert network.startswith("agent8088-egress-")
    assert ["docker", "network", "create", "--internal", network] in fake.calls
    run = next(call for call in fake.calls if call[1] == "run")
    assert "bridge" in run and "pypi.org" in run
    assert any(call[1:3] == ["network", "connect"] for call in fake.calls)
    assert any(arg.startswith("HTTPS_PROXY=http://agent8088-egress-proxy-") for arg in args)
    assert not engine._docker_network_closed()
    # Reused, not restarted, for the next command.
    engine._docker_egress_args()
    assert sum(1 for call in fake.calls if call[1] == "run") == 1


def test_docker_egress_failure_fails_closed(allowlist_mode, monkeypatch):
    engine = allowlist_mode
    monkeypatch.setattr(engine.subprocess, "run", _FakeDocker(fail_on="run"))
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    assert engine._docker_egress_args() == ("none", [])
    assert engine._docker_network_closed()
    assert engine._sandbox_network_summary("docker") == "blocked"
    assert "no network" in engine._sandbox_docker_note()


def test_docker_network_none_and_custom_values(engine, monkeypatch):
    monkeypatch.setattr(engine, "DOCKER_NETWORK", "none")
    assert engine._docker_egress_args() == ("none", [])
    monkeypatch.setattr(engine, "DOCKER_NETWORK", "bridge")
    assert engine._docker_egress_args() == ("bridge", [])
    assert "not filtered" in engine._sandbox_network_summary("docker")


def test_docker_run_uses_the_egress_network(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "_docker_sandbox_broken", False)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    monkeypatch.setattr(engine, "_docker_egress_args",
                        lambda: ("agent8088-egress-x", ["-e", "HTTPS_PROXY=http://p:3128"]))
    seen = {}
    monkeypatch.setattr(engine, "_exec_process",
                        lambda argv, timeout: seen.setdefault("argv", argv) and "ok")
    engine._exec_docker_command("pip install requests", 30, workspace=tmp_path)
    argv = seen["argv"]
    assert argv[argv.index("--network") + 1] == "agent8088-egress-x"
    assert "HTTPS_PROXY=http://p:3128" in argv


def test_windows_native_reports_an_unfiltered_network(engine, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    assert "not restricted" in engine._sandbox_network_summary("native")
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    failure = "curl: (6) Could not resolve host: example.org"
    notes = [engine._sandbox_network_note(failure, "curl https://example.org")
             for _ in range(4)]
    assert notes == ["", "", "", ""]  # not the sandbox's doing on Windows


def test_new_file_names_its_shell_path_under_docker(engine):
    capabilities.report(capabilities.SANDBOX, active="docker", preferred="native",
                        state=capabilities.DEGRADED)
    target = engine.ARTIFACTS_ROOT / "app" / "main.py"
    assert engine._docker_shell_path_hint(target) == (
        " (shell commands see it as /workspace/app/main.py)")
    assert "cannot see this file" in engine._docker_shell_path_hint(
        engine.PROJECT_ROOT / "README.md")
    capabilities.report(capabilities.SANDBOX, active="native", preferred="native",
                        state=capabilities.OK)
    assert engine._docker_shell_path_hint(target) == ""
