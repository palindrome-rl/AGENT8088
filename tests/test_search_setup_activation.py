"""Provisioning SearXNG must take effect in the process that provisioned it.

`/search setup` (and the WebUI equivalent) start the container, confirm the JSON
API answers, and write search_base_url. But the loopback SSRF exemption is derived
from search_base_url at import time, so a session that started without one kept an
empty exemption: _ssrf_check refused the very endpoint that had just been
provisioned, probe_searxng returned False, and /search status reported the healthy
backend as "no" while the chain stayed on ddgs. Restarting the CLI fixed it, which
is what made it look like SearXNG "was not detected".
"""
import pytest

from agent8088 import engine as A

SEARXNG = "http://127.0.0.1:8888/search?q="
PROBE = "http://127.0.0.1:8888/search?q=probe&format=json"


@pytest.fixture(autouse=True)
def _session_that_started_without_searxng(monkeypatch):
    monkeypatch.setattr(A, "SSRF_ALLOW_HOSTS", set(), raising=False)
    monkeypatch.setattr(A, "SSRF_ALLOW_PRIVATE", False, raising=False)
    monkeypatch.setattr(A, "_SEARCH_ALLOW_HOSTS", set(), raising=False)
    monkeypatch.setattr(A, "SEARCH_BASE_URL_CONFIGURED", False, raising=False)
    monkeypatch.setitem(A.APP_CONFIG, "search_base_url", "")


def test_the_endpoint_is_refused_before_setup_runs():
    # Guards the premise of every test below: without the exemption it is blocked.
    assert A._ssrf_check(PROBE)


def test_activating_the_endpoint_grants_the_loopback_exemption():
    A.activate_search_base_url(SEARXNG)
    assert A._SEARCH_ALLOW_HOSTS == {"127.0.0.1:8888"}
    assert A._ssrf_check(PROBE) is None


def test_activating_records_the_url_as_operator_intent():
    A.activate_search_base_url(SEARXNG)
    assert A.APP_CONFIG["search_base_url"] == SEARXNG
    assert A.SEARCH_BASE_URL_CONFIGURED is True
    # _search_config only surfaces the URL once it counts as configured.
    assert A._search_config()["search_base_url"] == SEARXNG


def test_the_search_context_handed_to_providers_can_reach_the_endpoint():
    A.activate_search_base_url(SEARXNG)
    assert A._search_context().check_url(PROBE) is None


def test_a_non_default_port_is_followed():
    A.activate_search_base_url("http://127.0.0.1:9999/search?q=")
    assert A._SEARCH_ALLOW_HOSTS == {"127.0.0.1:9999"}


def test_clearing_the_url_withdraws_the_exemption():
    A.activate_search_base_url(SEARXNG)
    A.activate_search_base_url("")
    assert A._SEARCH_ALLOW_HOSTS == set()
    assert A.SEARCH_BASE_URL_CONFIGURED is False
    assert A._ssrf_check(PROBE)


def test_a_remote_instance_gets_no_automatic_exemption():
    # Only loopback is granted automatically; a LAN host still needs ssrf_allow_hosts.
    A.activate_search_base_url("http://192.168.1.10:8888/search?q=")
    assert A._SEARCH_ALLOW_HOSTS == set()
    assert A.SEARCH_BASE_URL_CONFIGURED is True
