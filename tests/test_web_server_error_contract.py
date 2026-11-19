"""The web bridge's error contract.

Each test here pins a defect found in the end-to-end Web UI audit of
origin/development (2026-09-07) and now guards the fix. Findings W-06, W-07,
W-11 through W-16.

The contract in one line: a failure leaves this bridge as a real HTTP status
with an {"error": ...} body - never HTTP 200, and never a bare non-JSON 500.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "home" / "config.txt"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    from agent8088 import web_server
    from agent8088 import cli
    # cli may already be imported by another test, so environment changes alone
    # do not replace its module-level path. Never create sessions in user data.
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    monkeypatch.setattr(cli, 'SESSIONS_DIR', sessions)
    return TestClient(web_server.app)


# --- W-06: /api/tool/{name} has no exception handler -------------------------

def test_tool_invocation_returns_the_tools_error_not_a_500(client):
    """A missing file is an ordinary tool outcome, not a server crash.

    The CLI prints 'Could not read ...: No such file or directory'. The web
    endpoint used to let that ValueError escape the executor, so FastAPI
    returned a non-JSON 'Internal Server Error' and the UI could only say
    'Invocation failed (500)' - the real cause was destroyed.
    """
    response = client.post("/api/tool/read_text", json={"filename": "definitely-missing.txt"})
    assert response.status_code == 200
    assert "No such file" in response.json()["result"]


# --- W-07: no length validation on session names ----------------------------

def test_overlong_session_name_is_a_validation_error_not_a_500(client):
    response = client.post("/api/sessions/new", json={"name": "n" * 5000})
    assert response.status_code == 400, response.text
    assert "error" in response.json()


def test_session_name_at_the_limit_is_accepted(client):
    """The bound has to leave the documented maximum usable."""
    from agent8088 import cli
    response = client.post("/api/sessions/new", json={"name": "n" * cli.SESSION_NAME_MAX})
    assert response.status_code == 200, response.text


# --- W-02: error responses are HTTP 200 -------------------------------------

@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/api/mode", {"mode": "banana"}),
        ("post", "/api/sessions/resume", {"name": "no-such-session-xyz"}),
        ("post", "/api/tasks", {"goal": ""}),
        ("post", "/api/fusion/run", {"question": ""}),
        ("post", "/api/search/use", {"provider": "nope"}),
        ("delete", "/api/agents/no-such-agent", None),
        # Rename and delete kept answering 200 with an {"error": ...} body long
        # after the rest of the bridge was fixed. apiFetch only throws on
        # !res.ok, so onSuccess ran: the rename dialog closed as though the
        # rename had happened, and a failed delete reported nothing at all.
        ("patch", "/api/sessions/no-such-session-xyz", {"new_name": "whatever"}),
        ("delete", "/api/sessions/no-such-session-xyz", None),
    ],
)
def test_error_responses_use_a_4xx_status(client, method, path, payload):
    call = getattr(client, method)
    response = call(path, json=payload) if payload is not None else call(path)
    assert response.status_code >= 400, response.text
    assert "error" in response.json()


def test_renaming_onto_an_existing_session_is_a_conflict(client):
    from agent8088 import cli

    for name in ("alpha", "beta"):
        (cli.SESSIONS_DIR / f"{name}.json").write_text('{"messages": []}')
    response = client.patch("/api/sessions/alpha", json={"new_name": "beta"})
    assert response.status_code == 409, response.text
    assert "beta" in response.json()["error"]


def test_an_invalid_new_name_is_a_validation_error(client):
    from agent8088 import cli

    (cli.SESSIONS_DIR / "alpha.json").write_text('{"messages": []}')
    response = client.patch("/api/sessions/alpha", json={"new_name": "x" * 5000})
    assert response.status_code == 400, response.text


# --- W-08: unvalidated numeric settings -------------------------------------

def test_negative_max_turns_is_rejected(client):
    response = client.post("/api/limits", json={"key": "max_turns", "value": "-1"})
    assert "error" in response.json(), response.text


@pytest.mark.parametrize("temperature", [99, -5])
def test_out_of_range_temperature_is_rejected(client, temperature):
    response = client.post("/api/preferences", json={"temperature": temperature})
    assert "error" in response.json(), response.text


def test_tool_selection_is_persisted_and_validated(client, monkeypatch, tmp_path):
    from agent8088 import engine
    config_path = tmp_path / "config.txt"
    monkeypatch.setattr(engine, "CONFIG_PATH", config_path)
    monkeypatch.setattr(engine, "TOOL_SELECTION", "hybrid")
    response = client.post("/api/tool-selection", json={"mode": "full"})
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "full"
    assert config_path.read_text() == "tool_selection=full\n"
    assert client.get("/api/config").json()["tool_selection"] == "full"
    response = client.post("/api/tool-selection", json={"mode": "banana"})
    assert response.status_code == 400, response.text
    assert "error" in response.json()


def test_invalid_sandbox_mode_is_rejected(client):
    response = client.post("/api/sandbox", json={"mode": "banana"})
    assert "error" in response.json(), response.text


# --- W-09: silent success for things that do not exist ----------------------

def test_toggling_an_unknown_skill_is_an_error(client):
    response = client.post("/api/skills/no-such-skill-xyz/toggle", json={"enable": True})
    assert "error" in response.json(), response.text


def test_models_for_an_unknown_provider_does_not_return_another_providers_models(client):
    response = client.get("/api/models/no-such-provider-xyz")
    body = response.json()
    assert "error" in body or body.get("models") == [], response.text


# --- W-11: the SPA fallback swallows unmatched /api paths -------------------
# Only reproduces in production mode: _mount_static() installs a
# @app.get("/{full_path:path}") catch-all that answers *any* unmatched path -
# including /api/* - with index.html and HTTP 200. In --web-dev (and under a
# bare TestClient) no catch-all exists and the same request 404s correctly.


def test_unmatched_api_path_is_a_json_404_in_dev_mode(client):
    """Baseline: without the static mount, unmatched /api paths 404 properly."""
    response = client.get("/api/definitely-not-an-endpoint")
    assert response.status_code == 404, response.text


def test_unmatched_api_path_is_a_json_404_with_the_spa_mounted(monkeypatch, tmp_path):
    """Production mode: the SPA catch-all must not swallow /api/* paths."""
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "home" / "config.txt"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Agent8088</title>")

    from fastapi import FastAPI
    from agent8088 import web_server

    # Rebuild the production app shape without mutating the module-level app.
    app = FastAPI()
    app.router.routes = list(web_server.app.router.routes)
    web_server._mount_static(app, dist)

    response = TestClient(app).get("/api/definitely-not-an-endpoint")
    assert response.status_code == 404, (
        f"got {response.status_code} {response.headers.get('content-type')} "
        f"{response.text[:80]!r}")
