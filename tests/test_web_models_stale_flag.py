"""/api/models/{provider} says when it is serving the offline FALLBACK_MODELS
list instead of a live listing (stale/offline + reason), and /api/limits
says where the context window came from. No network: listing is stubbed."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "home" / "config.txt"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    from agent8088 import cli, web_server
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(cli, "SESSIONS_DIR", sessions)
    return TestClient(web_server.app), web_server._eng()


def _stub(monkeypatch, engine, *, fail):
    from agent8088 import providers
    monkeypatch.setitem(engine.PROVIDERS, "stubprov", {"model": "m"})
    monkeypatch.setattr(engine, "get_client", lambda name: (object(), None))

    def _list(provider, client, **kwargs):
        if fail:
            providers.LAST_LIST_ERROR[provider] = ConnectionError("connection refused")
            return ["offline-a", "offline-b"]
        providers.LAST_LIST_ERROR.pop(provider, None)
        return ["live-a"]

    monkeypatch.setattr(providers, "list_models", _list)


def test_live_listing_is_not_stale(client, monkeypatch):
    http, engine = client
    _stub(monkeypatch, engine, fail=False)
    body = http.get("/api/models/stubprov").json()
    assert body["models"] == ["live-a"]
    assert body["stale"] is False and body["offline"] is False
    assert body["source"] == "live" and body["reason"] == ""


def test_failed_listing_is_flagged_offline_with_reason(client, monkeypatch):
    http, engine = client
    _stub(monkeypatch, engine, fail=True)
    body = http.get("/api/models/stubprov").json()
    assert body["models"] == ["offline-a", "offline-b"]
    assert body["stale"] is True and body["offline"] is True
    assert body["source"] == "offline"
    assert "connection refused" in body["reason"]


def test_client_failure_is_flagged_offline(client, monkeypatch):
    http, engine = client
    monkeypatch.setitem(engine.PROVIDERS, "stubprov", {"model": "m"})

    def _boom(name):
        raise RuntimeError("no api key")

    monkeypatch.setattr(engine, "get_client", _boom)
    body = http.get("/api/models/stubprov").json()
    assert body["stale"] is True and "no api key" in body["reason"]


def test_limits_report_context_window_source(client, monkeypatch):
    http, engine = client
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a: (32768, 4096))
    monkeypatch.setattr(engine, "context_window_source", lambda *a: "default")
    body = http.get("/api/limits").json()
    assert body["active_model"]["context_window_source"] == "default"
