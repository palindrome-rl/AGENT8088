"""The address the SSRF guard vets must be the address urllib connects to.

_ssrf_check resolves the hostname and passes judgement; urllib then resolved
the same name again, independently. A DNS record under an attacker's control
with a short TTL answers public for the check and 127.0.0.1 for the connection,
and the body of a loopback service comes back to the agent -- including this
machine's own web bridge.

engine._browser_address_check already closes exactly this window for the
browsing proxy (its docstring names the attack). These tests pin the same
guarantee for the urllib paths: http_get/http_post and _fetch_url_bytes.
"""
from __future__ import annotations

import socket
import urllib.error

import pytest


def _addrinfo(ip: str, port: int = 80):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]


@pytest.fixture
def rebinding(engine, monkeypatch):
    """A host that passes the up-front SSRF check and then rebinds to loopback.

    _ssrf_check is stubbed to approve, which is precisely the state the real
    guard is left in when the first DNS answer is public: the question under
    test is what the connection itself does afterwards.
    """
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: _addrinfo("127.0.0.1", port or 80))
    return engine


def test_fetch_url_bytes_refuses_a_rebound_host(rebinding):
    data, error, _content_type = rebinding._fetch_url_bytes("http://rebind.example/payload")
    assert data is None
    assert "internal address" in error, error


def test_http_get_refuses_a_rebound_host(rebinding):
    spec = {"name": "get_page_title", "url": "{url}", "timeout": 5}
    result = rebinding._exec_http("http_get", spec, {"url": "http://rebind.example/"}, 5)
    assert "internal address" in result, result


def test_the_pinned_handler_rejects_every_internal_answer(rebinding):
    """Not just the first: a round-robin record mixing a public and a private
    address must not be usable by picking the convenient one."""
    monkeypatch_targets = _addrinfo("93.184.216.34") + _addrinfo("127.0.0.1")
    import agent8088.engine as engine_module

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: monkeypatch_targets)
    try:
        opener = engine_module._build_safe_opener()
        request = engine_module.urllib.request.Request("http://rebind.example/")
        with pytest.raises(urllib.error.URLError) as excinfo:
            opener.open(request, timeout=2)
        assert "internal address" in str(excinfo.value)
    finally:
        monkeypatch.undo()


def test_a_public_address_still_connects(engine, monkeypatch):
    """The guard must not break ordinary fetches: a public answer is pinned and
    used, and the connection is attempted against exactly that address."""
    attempted = {}

    def _fake_create_connection(address, *args, **kwargs):
        attempted["address"] = address
        raise OSError("no network in tests")

    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: _addrinfo("93.184.216.34", port or 80))
    monkeypatch.setattr(socket, "create_connection", _fake_create_connection)

    data, error, _ = engine._fetch_url_bytes("http://public.example/thing")
    assert data is None and "Fetch failed" in error
    assert attempted["address"][0] == "93.184.216.34", attempted
