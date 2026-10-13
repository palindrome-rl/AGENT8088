"""Session mutations must not race the worker using the shared conversation."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi import WebSocketDisconnect

from agent8088 import cli, web_server as W


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "S", cli.Session())
    monkeypatch.setattr(cli, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(W, "_cli", cli)
    cli.S.name = "original"
    cli.S.messages = [{"role": "user", "content": "keep me"}]
    cli._save_active_session()
    return TestClient(W.app)


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/sessions/new", {"name": "other"}),
    ("post", "/api/sessions/resume", {"name": "original"}),
    ("post", "/api/sessions/reset", {}),
    ("post", "/api/sessions/compact", {}),
    ("patch", "/api/sessions/original", {"new_name": "other"}),
    ("delete", "/api/sessions/original", None),
])
def test_session_mutations_refuse_an_active_turn(client, method, path, body):
    assert W._TURN_LOCK.acquire(blocking=False)
    try:
        response = client.request(method, path, json=body)
        assert response.status_code == 409, response.text
        assert response.json()["error"] == W.ANOTHER_TURN_RUNNING
        assert cli.S.name == "original"
        assert cli.S.messages == [{"role": "user", "content": "keep me"}]
        assert cli._session_path("original").exists()
    finally:
        W._TURN_LOCK.release()


def test_compaction_holds_the_turn_lock_until_the_model_finishes(client, monkeypatch):
    cli.S.messages *= 20
    held = []

    def summarize(*args, **kwargs):
        held.append(W._TURN_LOCK.locked())
        return "summary"

    monkeypatch.setattr(W, "_engine", SimpleNamespace(run_agent=summarize))
    response = client.post("/api/sessions/compact", json={"keep": 6})
    assert response.status_code == 200
    assert held == [True]
    assert not W._TURN_LOCK.locked()


def test_failed_session_request_releases_the_turn_lock(client):
    response = client.post("/api/sessions/resume", json={"name": "missing"})
    assert response.status_code == 404
    assert not W._TURN_LOCK.locked()
    assert client.post("/api/sessions/reset", json={}).status_code == 200


@pytest.mark.parametrize("kind,store_name,fields", [
    ("approval", "_pending_approvals", {"approved": True}),
    ("plan_approval", "_pending_plan_approvals", {"mode": "full-auto"}),
])
@pytest.mark.parametrize("owned", [False, True])
def test_websocket_can_only_answer_its_own_approval(monkeypatch, kind, store_name, fields, owned):
    class Socket:
        headers = {}

        async def accept(self):
            pass

        async def receive_json(self):
            if hasattr(self, "received"):
                store.pop("request", None)  # worker consumed the verdict
                raise WebSocketDisconnect()
            self.received = True
            return {"type": kind, "id": "request", **fields}

    ws = Socket()
    event = threading.Event()
    entry = {"event": event, "owner": id(ws) if owned else id(ws) + 1}
    store = {"request": entry}
    monkeypatch.setattr(W, store_name, store)
    monkeypatch.setattr(W, "_engine", object())
    monkeypatch.setattr(W, "_cli", object())
    asyncio.run(W.websocket_endpoint(ws))
    assert event.is_set() is owned
    for key, value in fields.items():
        assert entry.get(key) == (value if owned else None)


@pytest.mark.parametrize("command,args", [("new", "other"), ("plan", "")])
def test_commands_cannot_change_session_or_mode_during_another_turn(command, args):
    events = []

    async def send_json(event):
        events.append(event)

    ws = SimpleNamespace(send_json=send_json)
    assert W._TURN_LOCK.acquire(blocking=False)
    try:
        asyncio.run(W._handle_command(ws, {"command": command, "args": args}, object(), object()))
    finally:
        W._TURN_LOCK.release()
    assert events == [{"type": "error", "message": W.ANOTHER_TURN_RUNNING}]


def test_commands_hold_and_release_the_turn_lock(monkeypatch):
    from rich.console import Console

    held = []
    events = []

    async def send_json(event):
        events.append(event)

    def command(args):
        held.append(W._TURN_LOCK.locked())
        raise RuntimeError("failed command")

    c = SimpleNamespace(COMMANDS={"new": command}, console=Console(),
                        A=SimpleNamespace(PROVIDERS={}), WEB_CONFIRM=None,
                        S=cli.Session())
    asyncio.run(W._handle_command(SimpleNamespace(send_json=send_json),
                                {"command": "new", "args": "other"}, object(), c))
    assert held == [True]
    assert not W._TURN_LOCK.locked()
    assert c.WEB_CONFIRM is None
    assert "failed command" in events[-1]["result"]
