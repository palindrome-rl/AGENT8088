"""A small loopback-only HTTP/CONNECT proxy that runs the same host/IP-based
SSRF check on every request browser-use's Chromium makes, not just the first
navigation - see docs/superpowers/specs/2026-08-25-browser-use-integration-design.md
section 4 for why this exists (browser-use has no page.route()-style hook)."""
import http.client
import socket
import struct
import time

import pytest

from agent8088.browser_proxy import start_ssrf_filtering_proxy


def _connect_raw(port, target):
    """Send a raw CONNECT request and return (status_code, reason)."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    response = sock.recv(4096).decode(errors="replace")
    sock.close()
    status_line = response.splitlines()[0]
    _, code, *reason = status_line.split(" ", 2)
    return int(code), " ".join(reason)


def test_connect_to_blocked_target_is_refused():
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: "Blocked: test policy.")
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        code, reason = _connect_raw(port, "10.0.0.5:443")
        assert code == 403
        assert "Blocked" in reason
    finally:
        stop()


def test_connect_to_allowed_target_establishes_tunnel_and_relays_data():
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: None)
    port = int(proxy_url.rsplit(":", 1)[1])
    # Bind a local "upstream" server to CONNECT through to, so the test has no
    # real network dependency.
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    upstream_port = upstream.getsockname()[1]
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(f"CONNECT 127.0.0.1:{upstream_port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        conn, _ = upstream.accept()
        response = client.recv(4096)
        assert response.startswith(b"HTTP/1.1 200")
        # Prove the tunnel actually relays data in both directions, not just
        # that the handshake succeeded.
        client.sendall(b"ping")
        assert conn.recv(4096) == b"ping"
        conn.sendall(b"pong")
        assert client.recv(4096) == b"pong"
        client.close()
        conn.close()
    finally:
        stop()
        upstream.close()


def test_check_target_receives_a_url_shaped_string_for_connect():
    seen = []

    def check(url):
        seen.append(url)
        return "Blocked: test policy."

    proxy_url, stop = start_ssrf_filtering_proxy(check)
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        _connect_raw(port, "example.com:443")
    finally:
        stop()
    assert len(seen) == 1
    assert "example.com" in seen[0]
    assert "443" in seen[0]


def test_a_client_reset_mid_request_is_not_logged_as_a_crash(capsys):
    """Chromium routinely opens and abandons connections (speculative
    preconnects, cancelled requests). A hard RST while the proxy is still
    reading the request line used to print a full ConnectionResetError
    traceback to stderr, which reads as a crash even though nothing failed -
    forcing an RST (SO_LINGER with a zero timeout) reproduces exactly that."""
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: None)
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()  # RST arrives at the proxy before any request line is sent
        time.sleep(0.2)  # let the server thread observe and handle the reset
    finally:
        stop()
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "ConnectionResetError" not in captured.err


def test_plain_http_get_to_blocked_target_is_refused():
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: "Blocked: test policy.")
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "http://10.0.0.5/some/path")
        resp = conn.getresponse()
        assert resp.status == 403
    finally:
        stop()


@pytest.mark.parametrize("method", ["DELETE", "PATCH", "OPTIONS"])
def test_plain_http_delete_patch_options_are_forwarded_not_rejected_as_unsupported(method):
    """BaseHTTPRequestHandler answers 501 Unsupported method for any verb
    without a matching do_<VERB> - DELETE/PATCH/OPTIONS never got one, so a
    plain-HTTP page issuing a REST DELETE/PATCH call, or a CORS preflight
    OPTIONS, failed through this proxy regardless of the SSRF policy. Routing
    a *blocked* target proves it now reaches check_target - a 501 here means
    the request never got that far."""
    proxy_url, stop = start_ssrf_filtering_proxy(lambda url: "Blocked: test policy.")
    port = int(proxy_url.rsplit(":", 1)[1])
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, "http://10.0.0.5/some/path")
        resp = conn.getresponse()
        assert resp.status == 403
    finally:
        stop()


# ---------------------------------------------------------------------------
# DNS rebinding
# ---------------------------------------------------------------------------
def test_a_host_that_rebinds_to_loopback_after_the_check_is_refused():
    """The check and the connection must agree on one address.

    _ssrf_check resolves the hostname to decide whether it is internal, and
    the proxy then opened its upstream socket by *hostname* - a second,
    independent resolution. A short-TTL attacker record can answer "public"
    for the check and "127.0.0.1" a moment later for the connection, which
    handed the browsing agent the body of a private service. Verified as a
    working bypass before this test existed, against a real local server.
    """
    import http.server
    import socket
    import threading
    import urllib.request

    from agent8088 import engine as A

    class _Secret(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"INTERNAL SECRET"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    victim = http.server.HTTPServer(("127.0.0.1", 0), _Secret)
    threading.Thread(target=victim.serve_forever, daemon=True).start()
    victim_port = victim.server_address[1]

    rebind_host = "rebind.attacker.invalid"
    real_getaddrinfo = socket.getaddrinfo
    resolutions = []

    def rebinding_getaddrinfo(host, port, *args, **kwargs):
        if host != rebind_host:
            return real_getaddrinfo(host, port, *args, **kwargs)
        resolutions.append(host)
        # First answer public, every later one loopback.
        ip = "93.184.216.34" if len(resolutions) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]

    socket.getaddrinfo = rebinding_getaddrinfo
    proxy_url, stop = start_ssrf_filtering_proxy(
        lambda url: A._egress_check(url) or A._ssrf_check(url),
        check_address=A._browser_address_check,
    )
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url}))
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            opener.open(f"http://{rebind_host}:{victim_port}/", timeout=10)
        assert excinfo.value.code == 403
        assert "INTERNAL SECRET" not in excinfo.value.read().decode(errors="replace")
    finally:
        socket.getaddrinfo = real_getaddrinfo
        stop()
        victim.shutdown()


def test_an_ordinary_public_host_is_still_connected_to_the_address_checked():
    """Pinning must not break the normal path: a public hostname still
    resolves, passes, and connects."""
    import socket
    import threading

    from agent8088 import engine as A

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    seen = []

    def accept_once():
        conn, _ = server.accept()
        seen.append(conn.recv(1024))
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
        conn.close()

    threading.Thread(target=accept_once, daemon=True).start()

    # Allowlist this loopback endpoint the way an operator would, so the
    # public-path logic is exercised without leaving the machine.
    proxy_url, stop = start_ssrf_filtering_proxy(
        lambda url: None, check_address=lambda host, prt, ip: None)
    try:
        import urllib.request
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url}))
        response = opener.open(f"http://127.0.0.1:{port}/", timeout=10)
        assert response.read() == b"hi"
        assert seen and b"GET /" in seen[0]
    finally:
        stop()
        server.close()
