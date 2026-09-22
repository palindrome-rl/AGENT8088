"""Cross-Site WebSocket Hijacking guard: a page on another origin must not be
able to open a WS connection to this local server and ride the browser's
same-origin session. See _websocket_origin_allowed in web_server.py.

The check used to compare Origin against the request's own Host header, which
is self-referential and so useless against DNS rebinding: a page served from
http://evil.example:8180, with evil.example rebound to 127.0.0.1, sends an
Origin and a Host that agree with each other, and the handshake was accepted.
The allowed origins are now derived from the server's own loopback bind
instead -- which is also the only bind it will accept (see test_web_bind_host).
"""
from types import SimpleNamespace

import pytest

from agent8088 import web_server as W


def _fake_ws(origin=None, host=None):
    headers = {}
    if origin is not None:
        headers["origin"] = origin
    if host is not None:
        headers["host"] = host
    return SimpleNamespace(headers=headers)


@pytest.fixture(autouse=True)
def default_port(monkeypatch):
    monkeypatch.setattr(W, "_BIND_PORT", 8180)


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:8180",
    "http://localhost:8180",
    "http://[::1]:8180",
])
def test_loopback_origin_on_the_server_port_is_allowed(origin):
    assert W._websocket_origin_allowed(_fake_ws(origin=origin, host="127.0.0.1:8180"))


def test_origin_follows_a_custom_port(monkeypatch):
    """The allowed origin tracks --web-port, not a hardcoded 8180."""
    monkeypatch.setattr(W, "_BIND_PORT", 9000)
    assert W._websocket_origin_allowed(_fake_ws(origin="http://127.0.0.1:9000", host="127.0.0.1:9000"))


def test_vite_dev_proxy_origin_allowed():
    assert W._websocket_origin_allowed(_fake_ws(origin="http://127.0.0.1:5180", host="127.0.0.1:8180"))
    assert W._websocket_origin_allowed(_fake_ws(origin="http://localhost:5180", host="127.0.0.1:8180"))


def test_cross_origin_rejected():
    assert not W._websocket_origin_allowed(_fake_ws(origin="https://evil.example", host="127.0.0.1:8180"))


def test_rebound_origin_matching_the_host_header_is_rejected():
    """The DNS-rebinding case the old Host-reflective check let through:
    evil.example resolves to 127.0.0.1, so the browser's Origin and Host agree
    with each other while naming a host that is not this server."""
    assert not W._websocket_origin_allowed(
        _fake_ws(origin="http://evil.example:8180", host="evil.example:8180"))


def test_mismatched_port_rejected():
    """A different port on the same hostname is still a different origin."""
    assert not W._websocket_origin_allowed(_fake_ws(origin="http://127.0.0.1:9999", host="127.0.0.1:8180"))


def test_missing_origin_allowed_for_non_browser_clients():
    """Origin is a browser-enforced header; a native/non-browser caller
    (curl, a CLI tool) simply won't send one and can't be gated by it."""
    assert W._websocket_origin_allowed(_fake_ws(origin=None, host="127.0.0.1:8180"))
