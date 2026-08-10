"""Outbound secret guard.

_redact_secrets protects what comes BACK from a tool. This protects what goes
OUT: an http_post body, a browser URL. A secret in an outbound payload is never
legitimate, so this is a hard refusal with no escalation path.
"""
import pytest

from agent8088 import engine as A

FAKE_SECRET = "sk-live-abcdef0123456789"


@pytest.fixture(autouse=True)
def fake_secrets(monkeypatch):
    monkeypatch.setattr(A, "_SECRET_VALUES", [FAKE_SECRET])


def test_clean_payload_passes():
    assert A._outbound_secret_check("hello world") is None


def test_secret_in_body_is_blocked():
    reason = A._outbound_secret_check(f'{{"data": "{FAKE_SECRET}"}}')
    assert reason is not None
    assert FAKE_SECRET not in reason  # the guard must not echo the secret


def test_secret_in_url_query_is_blocked():
    reason = A._outbound_secret_check(f"https://example.com/x?k={FAKE_SECRET}")
    assert reason is not None


def test_empty_payload_passes():
    assert A._outbound_secret_check("") is None
    assert A._outbound_secret_check(None) is None


def test_short_values_are_never_treated_as_secrets(monkeypatch):
    """A 3-char config value must not make every payload a false positive."""
    monkeypatch.setattr(A, "_SECRET_VALUES", ["abc"])
    assert A._outbound_secret_check("abcdef") is None


# --- Integration: the guard must be reachable from the real tool paths ---

def test_http_post_carrying_a_secret_is_refused(engine, monkeypatch, register_tool):
    register_tool("probe_post", mode="http_post", args="query",
                  url="https://api.tavily.com/search?q={query_q}")
    monkeypatch.setattr(engine, "_SECRET_VALUES", [FAKE_SECRET])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")

    def _must_not_run(*a, **kw):
        raise AssertionError("_exec_http must not be reached with a secret payload")

    monkeypatch.setattr(engine, "_exec_http", _must_not_run)
    result = engine.run_tool("probe_post", {"query": FAKE_SECRET})
    assert "credential" in result.lower()


def test_full_auto_does_not_unlock_exfiltration(engine, monkeypatch):
    """Unlike other gates, no permission mode grants this."""
    monkeypatch.setattr(engine, "_SECRET_VALUES", [FAKE_SECRET])
    monkeypatch.setattr(engine, "_exec_browser", lambda *a, **kw: "PAGE")
    for mode in ("readonly", "edit", "full-auto"):
        monkeypatch.setattr(engine, "PERMISSION_MODE", mode)
        result = engine.run_tool(
            "browse_page", {"url": f"https://example.com/?leak={FAKE_SECRET}"})
        assert "credential" in result.lower(), f"not blocked in {mode} mode"


def test_clean_http_post_still_works(engine, monkeypatch, register_tool):
    register_tool("probe_post", mode="http_post", args="query",
                  url="https://api.tavily.com/search?q={query_q}")
    monkeypatch.setattr(engine, "_SECRET_VALUES", [FAKE_SECRET])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_exec_http", lambda *a, **kw: "OK")
    assert engine.run_tool("probe_post", {"query": "weather"}) == "OK"
