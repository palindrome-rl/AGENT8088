"""A self-hosted SearXNG on loopback must be reachable by web_search without
opening the whole loopback interface to every tool.

The shipped config.txt used to do this with `ssrf_allow_hosts=127.0.0.1,
localhost` - a blanket pass that also let the *browsing agent* visit any
service on the user's machine (a dev server, an admin panel, the local Ollama
API), and that silently turned off block_ip_addresses for the browser
profile, since that flag cannot coexist with an allowlist. Deriving the
exemption from the search endpoint the operator actually configured keeps
web_search working while leaving everything else on loopback refused.
"""
import pytest

from agent8088 import engine as A

SEARXNG = "http://127.0.0.1:8888/search?q="


@pytest.fixture(autouse=True)
def _no_operator_allowlist(monkeypatch):
    monkeypatch.setattr(A, "SSRF_ALLOW_HOSTS", set(), raising=False)
    monkeypatch.setattr(A, "SSRF_ALLOW_PRIVATE", False, raising=False)
    monkeypatch.setattr(A, "_SEARCH_ALLOW_HOSTS", set(), raising=False)


def test_the_configured_loopback_search_endpoint_is_allowed():
    assert A._local_search_allowance(SEARXNG) == {"127.0.0.1:8888"}


def test_a_non_default_search_port_is_followed():
    assert A._local_search_allowance("http://127.0.0.1:9999/search?q=") == {"127.0.0.1:9999"}


@pytest.mark.parametrize("base_url", [
    "",
    "   ",
    "https://searx.example.org/search?q=",   # public host: no exemption needed
    "http://127.0.0.1/search?q=",            # no explicit port to scope to
    "not a url at all",
])
def test_nothing_is_exempted_without_a_loopback_endpoint_and_port(base_url):
    assert A._local_search_allowance(base_url) == set()


def test_the_exempted_endpoint_passes_the_ssrf_check(monkeypatch):
    monkeypatch.setattr(A, "_SEARCH_ALLOW_HOSTS", {"127.0.0.1:8888"}, raising=False)

    assert A._ssrf_check("http://127.0.0.1:8888/search?q=hello") is None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:3000/",              # someone's dev server
    "http://127.0.0.1:11434/api/tags",     # the local Ollama API
    "http://localhost:8080/admin",
    "http://127.0.0.1:8889/search?q=x",    # neighbouring port, not the one configured
])
def test_every_other_loopback_service_stays_blocked(monkeypatch, url):
    monkeypatch.setattr(A, "_SEARCH_ALLOW_HOSTS", {"127.0.0.1:8888"}, raising=False)

    assert "Blocked:" in (A._ssrf_check(url) or "")


def test_the_search_exemption_does_not_weaken_the_browsing_deny_list(monkeypatch):
    """browse_page has no reason to reach SearXNG - web_search does. The
    exemption must therefore stay out of the browser profile entirely, or it
    would drop the loopback navigation patterns and disable
    block_ip_addresses just as the blanket allowlist did."""
    pytest.importorskip("browser_use")
    monkeypatch.setattr(A, "_SEARCH_ALLOW_HOSTS", {"127.0.0.1:8888"}, raising=False)

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert kwargs["block_ip_addresses"] is True
    assert "127.*" in kwargs["prohibited_domains"]
    assert "localhost" in kwargs["prohibited_domains"]


def test_the_shipped_config_no_longer_grants_blanket_loopback_access():
    """Regression guard on the packaged default itself."""
    import pathlib
    config = pathlib.Path(A.__file__).with_name("config.txt").read_text()
    active = [line.strip() for line in config.splitlines()
              if line.strip() and not line.strip().startswith("#")]

    assert not any(line.startswith("ssrf_allow_hosts=") for line in active), (
        "config.txt ships an active ssrf_allow_hosts entry again")
