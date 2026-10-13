"""Allowlist HTTP(S) proxy for the Docker sandbox fallback.

Runs in a small container attached to two Docker networks: the default bridge
(the internet) and an --internal network that sandbox containers join. A
sandbox container has no route out except through this proxy, so this is where
sandbox_allowed_domains is enforced for Docker -- the same list the native
runtime enforces itself.

    python egress_proxy.py PORT DOMAIN [DOMAIN ...]

Domains match exactly; `*.example.com` matches its subdomains (not the apex);
an optional `:port` suffix limits an entry to that port. Stdlib only: it is run
with `python -c` on the stock python image, not imported by Agent8088.
"""
import socket
import sys
import threading
from urllib.parse import urlsplit

IDLE_SECONDS = 300
MAX_HEAD_BYTES = 65536


def host_allowed(host: str, port: int, patterns) -> bool:
    host = host.lower().rstrip(".")
    for pattern in patterns:
        name, _, want_port = pattern.strip().lower().partition(":")
        if want_port and want_port != str(port):
            continue
        if name.startswith("*.") and host.endswith(name[1:]):
            return True
        if host == name:
            return True
    return False


def parse_target(method: str, target: str):
    """(host, port, origin-form path) for a proxy request; path is None for CONNECT."""
    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        return host.strip("[]"), int(port), None
    parts = urlsplit(target)
    if parts.scheme != "http" or not parts.hostname:
        raise ValueError(f"not an absolute http URL: {target}")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.hostname, parts.port or 80, path


def _read_head(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk or len(data) + len(chunk) > MAX_HEAD_BYTES:
            return None, b""
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head.decode("latin-1"), rest


def _reply(conn, status: str, text: str) -> None:
    body = text.encode("utf-8")
    conn.sendall(f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                 f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                 + body)


def _pipe(client, upstream) -> None:
    def copy(src, dst):
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

    back = threading.Thread(target=copy, args=(upstream, client), daemon=True)
    back.start()
    copy(client, upstream)
    back.join()


def handle(conn, patterns) -> None:
    upstream = None
    try:
        conn.settimeout(IDLE_SECONDS)
        head, rest = _read_head(conn)
        if not head:
            return
        lines = head.split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        method = method.upper()
        try:
            host, port, path = parse_target(method, target)
        except ValueError:
            _reply(conn, "400 Bad Request", "Agent8088 sandbox proxy: send an absolute "
                   "http:// URL or a CONNECT request.\n")
            return
        if not host_allowed(host, port, patterns):
            _reply(conn, "403 Forbidden", f"Agent8088 sandbox: {host} is not in "
                   "sandbox_allowed_domains.\n")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except OSError as exc:
            _reply(conn, "502 Bad Gateway", f"Agent8088 sandbox proxy could not reach "
                   f"{host}:{port}: {exc}\n")
            return
        upstream.settimeout(IDLE_SECONDS)
        if path is None:
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            # Origin-form request line, one request per connection: the next
            # request may name another host, which has to be checked again.
            headers = [line for line in lines[1:] if not line.lower().startswith(
                ("proxy-connection:", "proxy-authorization:", "connection:"))]
            head = "\r\n".join([f"{method} {path} {version}", *headers,
                                "Connection: close", "", ""])
            upstream.sendall(head.encode("latin-1"))
        if rest:
            upstream.sendall(rest)
        _pipe(conn, upstream)
    except (OSError, ValueError):
        pass
    finally:
        for sock in (conn, upstream):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def serve(port: int, patterns) -> None:
    server = socket.create_server(("0.0.0.0", port))
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle, args=(conn, patterns), daemon=True).start()


if __name__ == "__main__":
    serve(int(sys.argv[1]), sys.argv[2:])
