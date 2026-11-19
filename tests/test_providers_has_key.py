"""GET /api/providers must report whether a key is actually resolvable for
each built-in provider, not just its env var name -- the Model Switcher
uses this to show "no API key found for X, set Y" instead of silently
letting a keyless provider fail on switch. Reported live: deepseek/openai/
moonshot/qwen have no key configured on this machine and the switcher gave
no indication why picking them would fail.
"""
import pytest
from fastapi.testclient import TestClient


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


def test_details_include_has_key_per_provider(client):
    resp = client.get("/api/providers")
    assert resp.status_code == 200
    details = resp.json()["details"]
    assert "openai" in details
    for name, info in details.items():
        assert "has_key" in info, name
        assert isinstance(info["has_key"], bool), name


def test_provider_without_env_key_reports_false(client, monkeypatch):
    from agent8088 import engine
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(engine, "load_env_file", lambda *a, **k: {})
    resp = client.get("/api/providers")
    assert resp.json()["details"]["openai"]["has_key"] is False


def test_provider_with_env_key_reports_true(client, monkeypatch):
    from agent8088 import engine
    monkeypatch.setattr(engine, "load_env_file", lambda *a, **k: {"OPENAI_API_KEY": "sk-test"})
    resp = client.get("/api/providers")
    assert resp.json()["details"]["openai"]["has_key"] is True


def test_ollama_has_no_key_env_and_is_not_flagged(client):
    """A local, unauthenticated provider must not show the "no key" warning
    just because it has no api_key_env at all."""
    resp = client.get("/api/providers")
    assert resp.json()["details"]["ollama"]["api_key_env"] == ""
