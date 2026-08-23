"""A server that completes the handshake, sends headers, then never finishes.

This is the exact shape Invoke-BoundedDownload exists to survive, and the reason a
plain `Invoke-WebRequest -TimeoutSec` is not enough: the TCP connect succeeds, the
response headers arrive promptly, and only the BODY stalls. Nothing before the body
read can detect it, which is why -TimeoutSec (headers only on 5.1, SendAsync only on
7.x) lets the installer hang indefinitely.

Prints the chosen port on stdout, then serves until killed.
"""
import socket
import sys
import threading
import time

CHUNK_INTERVAL = 5.0          # long enough that any sane timeout fires first
CLAIMED_LENGTH = 100 * 1024 * 1024


def _serve(conn: socket.socket) -> None:
    try:
        conn.recv(65536)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: %d\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n" % CLAIMED_LENGTH
        )
        while True:
            # One byte at a time: the connection stays alive and the transfer stays
            # technically in progress, so only a wall clock can end it.
            conn.sendall(b"\0")
            time.sleep(CHUNK_INTERVAL)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    print(srv.getsockname()[1], flush=True)
    while True:
        try:
            conn, _ = srv.accept()
        except Exception:
            return 0
        threading.Thread(target=_serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
