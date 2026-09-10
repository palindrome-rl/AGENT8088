"""The startup probe must not call a healthy SearXNG dead just because it was slow.

STARTUP_PROBE_TIMEOUT was a flat 3s, but a SearXNG search fans out to every
configured engine, so the probe's own query is inherently slow -- measured at
0.96s-3.0s against a local container. Sitting on the boundary made startup
selection a coin flip: a probe that overran picked ddgs and pinned it for the
whole session, while /search status (probing again later) reported searxng
"available". That contradiction is what made the symptom look random.

A longer timeout is nearly free for a *loopback* instance, because a stopped
container refuses the connection instantly (measured 0.0002s) rather than
hanging. A remote instance is different: a firewall that drops packets makes
the probe hang for the full timeout, on the startup path. So the budget is
generous for loopback and stays short for everything else.
"""
import pytest

from agent8088 import web_search


class _Ctx:
    """Minimal SearchContext stand-in: records the timeout the probe asked for."""

    def __init__(self, base_url, payload=None, raises=None):
        self.config = {"search_base_url": base_url}
        self.payload = {"results": []} if payload is None else payload
        self.raises = raises
        self.seen_timeout = None

    def check_url(self, url):
        return None  # allowed


@pytest.fixture
def spy(monkeypatch):
    calls = {}

    def fake_get_json(url, timeout=None):
        calls["url"] = url
        calls["timeout"] = timeout
        return calls.get("payload", {"results": []})

    monkeypatch.setattr(web_search, "_http_get_json", fake_get_json)
    return calls


LOOPBACK = "http://127.0.0.1:8888/search?q="
REMOTE = "https://searx.example.org/search?q="


def test_a_loopback_instance_gets_the_generous_budget(spy):
    assert web_search.probe_searxng(_Ctx(LOOPBACK)) is True
    assert spy["timeout"] == web_search.LOCAL_PROBE_TIMEOUT
    assert spy["timeout"] > web_search.STARTUP_PROBE_TIMEOUT


def test_localhost_by_name_counts_as_loopback(spy):
    web_search.probe_searxng(_Ctx("http://localhost:8888/search?q="))
    assert spy["timeout"] == web_search.LOCAL_PROBE_TIMEOUT


def test_a_remote_instance_keeps_the_short_budget(spy):
    # A remote host can black-hole packets; startup must not hang on it.
    web_search.probe_searxng(_Ctx(REMOTE))
    assert spy["timeout"] == web_search.STARTUP_PROBE_TIMEOUT


def test_the_generous_budget_clears_the_measured_worst_case(spy):
    # Observed range against a local container was 0.96s-3.0s.
    assert web_search.LOCAL_PROBE_TIMEOUT >= 6


def test_an_explicit_timeout_still_wins(spy):
    web_search.probe_searxng(_Ctx(LOOPBACK), timeout=1)
    assert spy["timeout"] == 1


def test_an_unconfigured_endpoint_is_not_probed(spy):
    assert web_search.probe_searxng(_Ctx("")) is False
    assert "timeout" not in spy


def test_a_payload_without_a_results_list_is_not_usable(monkeypatch):
    monkeypatch.setattr(web_search, "_http_get_json",
                        lambda url, timeout=None: {"error": "json disabled"})
    assert web_search.probe_searxng(_Ctx(LOOPBACK)) is False


def test_a_refused_connection_is_not_usable(monkeypatch):
    def boom(url, timeout=None):
        raise OSError("Connection refused")
    monkeypatch.setattr(web_search, "_http_get_json", boom)
    assert web_search.probe_searxng(_Ctx(LOOPBACK)) is False


def test_a_blocked_url_is_never_requested(monkeypatch):
    called = []
    monkeypatch.setattr(web_search, "_http_get_json",
                        lambda url, timeout=None: called.append(url))
    ctx = _Ctx(LOOPBACK)
    ctx.check_url = lambda url: "Blocked: internal address"
    assert web_search.probe_searxng(ctx) is False
    assert called == []
