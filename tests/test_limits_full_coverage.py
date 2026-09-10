"""Every key shown in the Limits panel (terminal /limits and WebUI) must
actually be settable through set_limit -- context_window,
max_completion_tokens, max_tool_timeout_seconds, and denial_breaker_threshold
were displayed as read-only info (both /limits and GET /api/limits read the
live globals directly) but were never added to LIMIT_SPECS, so POST
/api/limits (and the terminal's /limits <key> <value>) raised KeyError for
all four -- reported live as "Limits section not able to update values."
"""
import pytest
from fastapi.testclient import TestClient


def test_all_four_previously_missing_keys_are_now_settable(engine):
    for key, value in [
        ("context_window", "100000"),
        ("max_completion_tokens", "32000"),
        ("max_tool_timeout_seconds", "300"),
        ("denial_breaker_threshold", "5"),
    ]:
        result = engine.set_limit(key, value)
        assert result["key"] == key
        assert result["new"] == int(value)


def test_context_window_change_is_live_immediately(engine):
    engine.set_limit("context_window", "111111")
    assert engine.CONTEXT_WINDOW == 111111


def test_max_completion_tokens_change_is_live_immediately(engine):
    engine.set_limit("max_completion_tokens", "22222")
    assert engine.MAX_COMPLETION_TOKENS == 22222


def test_max_tool_timeout_seconds_change_is_live_immediately(engine):
    engine.set_limit("max_tool_timeout_seconds", "45")
    assert engine.MAX_TOOL_TIMEOUT_SECONDS == 45


def test_denial_breaker_threshold_change_is_live_immediately(engine):
    engine.set_limit("denial_breaker_threshold", "9")
    assert engine.DENIAL_BREAKER_THRESHOLD == 9


def test_negative_value_still_rejected_for_new_keys(engine):
    with pytest.raises(ValueError):
        engine.set_limit("context_window", "-1")


def test_unknown_key_still_raises_keyerror(engine):
    with pytest.raises(KeyError):
        engine.set_limit("not_a_real_limit", "1")


# --- end-to-end through the WebUI's actual POST /api/limits ---------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "home" / "config.txt"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    from agent8088 import web_server, cli
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(cli, "SESSIONS_DIR", sessions)
    return TestClient(web_server.app)


@pytest.mark.parametrize("key,value", [
    ("context_window", "77777"),
    ("max_completion_tokens", "12345"),
    ("max_tool_timeout_seconds", "60"),
    ("denial_breaker_threshold", "7"),
])
def test_post_api_limits_updates_each_previously_broken_key(client, key, value):
    resp = client.post("/api/limits", json={"key": key, "value": value})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["new"] == int(value)

    got = client.get("/api/limits").json()
    assert got[key] == int(value)
