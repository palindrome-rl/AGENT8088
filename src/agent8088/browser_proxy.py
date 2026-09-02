"""Loopback-only HTTP/CONNECT forward proxy that runs a caller-supplied
host/IP check on every request before forwarding it.

browser-use (the interactive-browsing library _exec_browser delegates to)
has no per-request interception hook equivalent to Playwright's page.route(),
which is what the old single-shot browse_page used to run _egress_check and
_ssrf_check against every request the page made, not just the first
navigation. This proxy restores that guarantee at the network layer instead:
point browser-use's ProxySettings at it and every request - initial nav,
redirects, clicked links, form posts - passes through the same check.

Both existing checks are purely hostname/resolved-IP based (no path or query
dependency), so a CONNECT-level proxy has exactly the granularity needed.
"""
import http.server
import socket
import socketserver
import threading
from typing import Callable, Optional, Tuple


class _SSRFFilteringHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # silence default request logging to stderr

    def _connect_upstream(self, host, port):
        """Open the upstream socket to an address the guard has approved.

        Handing a *hostname* to socket.create_connection would resolve it a
        second time, independently of the resolution the SSRF check just
        validated - a DNS-rebinding window in which an attacker-controlled
        record answers "public" for the check and "127.0.0.1" for the
        connection. So resolve once here and check that exact address before
        connecting to it.

        Returns (socket, status, message): status is None on success, 403 for
        an address the guard refused, 502 for a real connectivity failure.
        """
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            return None, 502, f"Could not resolve {host}: {exc}"

        last_error = None
        for family, socktype, proto, _canonname, sockaddr in addresses:
            blocked = self.server.check_address(host, port, sockaddr[0])
            if blocked:
                return None, 403, blocked
            upstream = socket.socket(family, socktype, proto)
            try:
                upstream.settimeout(10)
                upstream.connect(sockaddr)
                return upstream, None, ""
            except OSError as exc:
                last_error = exc
                upstream.close()
        return None, 502, f"Could not connect to {host}:{port}: {last_error}"

    def do_CONNECT(self):
        host, _, port_str = self.path.partition(":")
        port = int(port_str or 443)
        blocked = self.server.check_target(f"https://{host}:{port}/")
        if blocked:
            self.send_error(403, blocked)
            return
        upstream, status, message = self._connect_upstream(host, port)
        if upstream is None:
            self.send_error(status, message)
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._relay(self.connection, upstream)

    def _do_forward(self, method):
        blocked = self.server.check_target(self.path)
        if blocked:
            self.send_error(403, blocked)
            return
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        host = parsed.hostname
        port = parsed.port or 80
        if not host:
            self.send_error(400, "Malformed request target")
            return
        upstream, status, message = self._connect_upstream(host, port)
        if upstream is None:
            self.send_error(status, message)
            return
        target = urllib.parse.urlunparse(
            ("", "", parsed.path or "/", parsed.params, parsed.query, ""))
        upstream.sendall(f"{method} {target} HTTP/1.1\r\n".encode())
        for key, value in self.headers.items():
            if key.lower() == "proxy-connection":
                continue
            upstream.sendall(f"{key}: {value}\r\n".encode())
        upstream.sendall(b"\r\n")
        content_length = int(self.headers.get("Content-Length", 0) or 0)
        if content_length:
            upstream.sendall(self.rfile.read(content_length))
        self._relay(self.connection, upstream)

    def do_GET(self):
        self._do_forward("GET")

    def do_POST(self):
        self._do_forward("POST")

    def do_HEAD(self):
        self._do_forward("HEAD")

    def do_PUT(self):
        self._do_forward("PUT")

    def do_DELETE(self):
        self._do_forward("DELETE")

    def do_PATCH(self):
        self._do_forward("PATCH")

    def do_OPTIONS(self):
        self._do_forward("OPTIONS")

    @staticmethod
    def _relay(client_sock, upstream_sock):
        def pipe(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t1 = threading.Thread(target=pipe, args=(client_sock, upstream_sock), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream_sock, client_sock), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        upstream_sock.close()


class _SSRFFilteringProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, check_target: Callable[[str], Optional[str]],
                 check_address: Callable[[str, int, str], Optional[str]]):
        super().__init__(("127.0.0.1", 0), _SSRFFilteringHandler)
        self.check_target = check_target
        self.check_address = check_address

    def handle_error(self, request, client_address):
        # Chromium routinely opens and abandons connections (speculative
        # preconnects, cancelled requests) - socketserver's default
        # handle_error() prints the resulting ConnectionResetError/
        # BrokenPipeError as a full traceback to stderr, which reads as a
        # crash even though nothing actually failed. Suppress just those
        # two expected, harmless cases; anything else still prints normally
        # so a genuine bug in the proxy isn't silenced.
        import sys
        exc_type = sys.exc_info()[0]
        if exc_type is not None and issubclass(exc_type, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def start_ssrf_filtering_proxy(
    check_target: Callable[[str], Optional[str]],
    check_address: Optional[Callable[[str, int, str], Optional[str]]] = None,
) -> Tuple[str, Callable[[], None]]:
    """Start a loopback-only proxy that runs `check_target(url)` (returning
    None if allowed, else an error string - the same contract as
    _egress_check/_ssrf_check) before forwarding every request.

    `check_address(host, port, ip)` is the second half of that guarantee, and
    follows the same return contract. The proxy resolves each target once and
    passes the resolved IP through it before connecting to that exact
    address, so the check and the connection can never disagree - without it,
    a hostname was resolved a second time at connect and an attacker-
    controlled short-TTL record could answer "public" for the check and
    "127.0.0.1" for the connection. Defaults to refusing nothing, which is
    only appropriate when the caller has no address policy at all.

    Returns (proxy_url, stop_fn). Call stop_fn() to shut the proxy down."""
    server = _SSRFFilteringProxyServer(
        check_target, check_address or (lambda host, port, ip: None))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def stop():
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{port}", stop
