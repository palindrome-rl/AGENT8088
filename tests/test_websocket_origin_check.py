"""Cross-Site WebSocket Hijacking guard: a page on another origin must not be
able to open a WS connection to this local server and ride the browser's
same-origin session. See _websocket_origin_allowed in web_server.py.
"""
from types import SimpleNamespace

from agent8088 import web_server as W


def _fake_ws(origin=None, host=None):
    headers = {}
    if origin is not None:
        headers["origin"] = origin
    if host is not None:
        headers["host"] = host
    return SimpleNamespace(headers=headers)


def test_same_origin_as_server_host_allowed():
    assert W._websocket_origin_allowed(_fake_ws(origin="http://127.0.0.1:8180", host="127.0.0.1:8180"))


def test_same_origin_with_custom_bind_host_allowed():
    """Same-origin check must work for any --web-host/--web-port the user
    configured, not just the default 127.0.0.1:8180."""
    assert W._websocket_origin_allowed(_fake_ws(origin="http://192.168.1.50:9000", host="192.168.1.50:9000"))


def test_vite_dev_proxy_origin_allowed():
    assert W._websocket_origin_allowed(_fake_ws(origin="http://127.0.0.1:5180", host="127.0.0.1:8180"))
    assert W._websocket_origin_allowed(_fake_ws(origin="http://localhost:5180", host="127.0.0.1:8180"))


def test_cross_origin_rejected():
    assert not W._websocket_origin_allowed(_fake_ws(origin="https://evil.example", host="127.0.0.1:8180"))


def test_mismatched_port_rejected():
    """A different port on the same hostname is still a different origin."""
    assert not W._websocket_origin_allowed(_fake_ws(origin="http://127.0.0.1:9999", host="127.0.0.1:8180"))


def test_missing_origin_allowed_for_non_browser_clients():
    """Origin is a browser-enforced header; a native/non-browser caller
    (curl, a CLI tool) simply won't send one and can't be gated by it."""
    assert W._websocket_origin_allowed(_fake_ws(origin=None, host="127.0.0.1:8180"))
