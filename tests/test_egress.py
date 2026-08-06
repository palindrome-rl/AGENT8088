"""Egress domain policy: which PUBLIC hosts the agent may reach.

_ssrf_check blocks internal addresses; this is the complementary control for
public ones. Empty allowlist = allow all (the unchanged default), so these
tests set the policy explicitly.
"""
import pytest

from agent8088 import engine as A


@pytest.fixture
def policy(monkeypatch):
    def _set(allowed=(), blocked=()):
        monkeypatch.setattr(A, "EGRESS_ALLOWED_DOMAINS", list(allowed))
        monkeypatch.setattr(A, "EGRESS_BLOCKED_DOMAINS", list(blocked))
    return _set


def test_empty_allowlist_permits_everything(policy):
    policy()
    assert A._egress_check("https://example.com/x") is None


def test_blocklist_always_enforced(policy):
    policy(blocked=["pastebin.com"])
    assert A._egress_check("https://pastebin.com/raw/x") is not None
    assert A._egress_check("https://example.com/x") is None


def test_blocklist_matches_subdomains(policy):
    policy(blocked=["pastebin.com"])
    assert A._egress_check("https://raw.pastebin.com/x") is not None


def test_blocklist_does_not_match_lookalike_suffix(policy):
    """evilpastebin.com must NOT be treated as a subdomain of pastebin.com."""
    policy(blocked=["pastebin.com"])
    assert A._egress_check("https://evilpastebin.com/x") is None


def test_allowlist_denies_unlisted_host(policy):
    policy(allowed=["example.com", "api.github.com"])
    assert A._egress_check("https://example.com/x") is None
    assert A._egress_check("https://docs.example.com/x") is None  # subdomain ok
    assert A._egress_check("https://evil.test/x") is not None


def test_allowlist_is_case_insensitive(policy):
    policy(allowed=["example.com"])
    assert A._egress_check("https://EXAMPLE.COM/x") is None


def test_blocklist_wins_over_allowlist(policy):
    policy(allowed=["example.com"], blocked=["example.com"])
    assert A._egress_check("https://example.com/x") is not None


def test_malformed_url_is_blocked(policy):
    policy(allowed=["example.com"])
    assert A._egress_check("not a url") is not None


def test_error_names_the_config_key(policy):
    policy(allowed=["example.com"])
    assert "allowed_domains" in A._egress_check("https://evil.test/x")
    policy(blocked=["pastebin.com"])
    assert "blocked_domains" in A._egress_check("https://pastebin.com/x")


# --- Integration: the policy must be reachable from the real tool paths ---

def test_http_tool_is_blocked_by_policy(engine, monkeypatch):
    """run_tool's http gate consults the egress policy before any request."""
    monkeypatch.setattr(engine, "EGRESS_BLOCKED_DOMAINS", ["api.tavily.com"])
    monkeypatch.setattr(engine, "EGRESS_ALLOWED_DOMAINS", [])

    def _must_not_run(*a, **kw):
        raise AssertionError("_exec_http must not be reached for a blocked domain")

    monkeypatch.setattr(engine, "_exec_http", _must_not_run)
    result = engine.run_tool("web_search_tavily", {"query": "x"})
    assert "blocked_domains" in result


def test_browser_tool_is_blocked_by_policy(engine, monkeypatch):
    monkeypatch.setattr(engine, "EGRESS_ALLOWED_DOMAINS", ["example.com"])
    monkeypatch.setattr(engine, "EGRESS_BLOCKED_DOMAINS", [])

    def _must_not_run(*a, **kw):
        raise AssertionError("_exec_browser must not be reached for a denied host")

    monkeypatch.setattr(engine, "_exec_browser", _must_not_run)
    result = engine.run_tool("browse_page", {"url": "https://evil.test/x"})
    assert "allowed_domains" in result


def test_http_tool_allowed_when_host_is_listed(engine, monkeypatch):
    monkeypatch.setattr(engine, "EGRESS_ALLOWED_DOMAINS", ["api.tavily.com"])
    monkeypatch.setattr(engine, "EGRESS_BLOCKED_DOMAINS", [])
    monkeypatch.setattr(engine, "_exec_http", lambda *a, **kw: "OK")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    assert engine.run_tool("web_search_tavily", {"query": "x"}) == "OK"
