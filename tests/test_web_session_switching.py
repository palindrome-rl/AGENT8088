"""Switching Web UI chats while a task runs (SESSION-FIX-DESIGN.md, Fix 1).

The viewed chat (cli.S) and the chat that owns the running turn are separate:
New chat / Resume work mid-run, and the turn's messages, saves, approvals and
events stay with the chat it started in. Fake engine, no model, no network.
"""
import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agent8088 import cli, web_server as W


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


class _Engine:
    class AgentInterrupted(Exception):
        pass

    def __init__(self, root, body):
        self.PERMISSION_MODE = "full-auto"
        self.ARTIFACTS_ROOT = root
        self.body = body
        self.granted = 0

    def build_tools_def(self, specs):
        return []

    def run_agent(self, messages, **kwargs):
        return self.body(messages, kwargs, self)

    def current_rate_limit_status(self, provider):
        return None

    def grant_escalation(self, *args):
        self.granted += 1


def _file(name):
    return json.loads(cli._session_path(name).read_text(encoding="utf-8"))


def _wait_for(condition, timeout=5.0):
    deadline = time.time() + timeout
    while not condition():
        assert time.time() < deadline, "timed out"
        time.sleep(0.01)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "S", cli.Session())
    monkeypatch.setattr(cli, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(W, "_cli", cli)
    monkeypatch.setattr(W, "_validated_attachments", lambda *a, **k: [])
    client = TestClient(W.app)
    assert client.post("/api/sessions/new", json={"name": "chat-b"}).status_code == 200
    cli.S.messages = [{"role": "user", "content": "b question"},
                      {"role": "assistant", "content": "b answer"}]
    W._save_session(cli, cli.S)
    response = client.post("/api/sessions/new", json={"name": "chat-a"})
    assert response.status_code == 200
    env = type("Env", (), {})()
    env.client, env.tmp = client, tmp_path
    env.a_id = response.json()["id"]
    env.b_id = _file("chat-b")["id"]
    yield env
    W._running.clear()


def _start(env, body, msg=None):
    """Run one chat turn on a background thread, as the WebSocket would."""
    engine, ws = _Engine(env.tmp, body), _Socket()
    frame = msg or {"text": "do the task"}
    thread = threading.Thread(
        target=lambda: asyncio.run(W._handle_chat(ws, frame, engine, cli)), daemon=True)
    thread.start()
    _wait_for(lambda: W._running or not thread.is_alive())
    return thread, ws, engine


def _gated_body(gate, started):
    def body(messages, kwargs, engine):
        started.set()
        assert gate.wait(5)
        messages.append({"role": "assistant", "content": "A result"})
        kwargs["on_token"]("content", "A result")
        kwargs["on_answer"]("A result")
        return "A result"
    return body


def test_new_chat_and_resume_work_while_a_task_runs(env):
    gate, started = threading.Event(), threading.Event()
    thread, ws, _ = _start(env, _gated_body(gate, started))
    assert started.wait(5)
    running = cli.S

    assert env.client.post("/api/sessions/new", json={"name": ""}).status_code == 200
    assert cli.S is not running and cli.S.messages == []
    response = env.client.post("/api/sessions/resume", json={"name": "chat-b"})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == env.b_id
    assert cli.S.name == "chat-b"
    status = env.client.get("/api/status").json()
    assert status["session_id"] == env.b_id
    assert status["running_session"] == {"id": env.a_id, "name": "chat-a"}

    gate.set()
    thread.join(5)
    assert not thread.is_alive()
    # Switching did not interrupt or redirect the task: it finished in chat A.
    assert running.messages[-1] == {"role": "assistant", "content": "A result"}
    assert not W._TURN_LOCK.locked() and not W._running


def test_output_saves_to_the_running_chat_never_the_viewed_one(env):
    gate, started = threading.Event(), threading.Event()
    thread, ws, _ = _start(env, _gated_body(gate, started))
    assert started.wait(5)
    env.client.post("/api/sessions/resume", json={"name": "chat-b"})
    gate.set()
    thread.join(5)

    a = _file("chat-a")
    assert a["id"] == env.a_id
    assert [m["content"] for m in a["messages"]] == ["do the task", "A result"]
    b = _file("chat-b")
    assert [m["content"] for m in b["messages"]] == ["b question", "b answer"]
    history = env.client.get("/api/history").json()
    assert history["session_id"] == env.b_id and history["running"] is False
    assert all("A result" not in str(m) for m in history["messages"])

    # Every streamed event names chat A, so the browser can route it.
    assert ws.events and all(e.get("session_id") == env.a_id for e in ws.events)
    assert {"token", "answer", "session_saved"} <= {e["type"] for e in ws.events}


def test_approval_identifies_its_chat_and_can_be_answered_after_switching_back(env):
    verdicts = []

    def body(messages, kwargs, engine):
        verdicts.append(kwargs["on_escalation"](
            "write_file", "ESCALATION_REQUEST\x1fworkspace-write\x1fwrite\x1freport.md\x1fwrite report.md"))
        return "ok"

    thread, ws, engine = _start(env, body)
    _wait_for(lambda: W._pending_approvals)
    (esc_id, entry), = W._pending_approvals.items()
    assert entry["session_id"] == env.a_id

    env.client.post("/api/sessions/resume", json={"name": "chat-b"})
    assert env.client.get("/api/history").json()["pending_approvals"] == []
    env.client.post("/api/sessions/resume", json={"name": "chat-a"})
    history = env.client.get("/api/history").json()
    assert history["running"] is True
    assert [p["id"] for p in history["pending_approvals"]] == [esc_id]
    assert history["pending_approvals"][0]["session_id"] == env.a_id

    entry["approved"] = True   # what the owning socket's "approval" frame does
    entry["event"].set()
    thread.join(5)
    assert verdicts == [True] and engine.granted == 1
    escalation = next(e for e in ws.events if e["type"] == "escalation")
    assert escalation["session_id"] == env.a_id


def test_stop_ends_the_running_task_and_keeps_its_progress_in_its_chat(env):
    def body(messages, kwargs, engine):
        messages.append({"role": "assistant", "content": "step 1 done"})
        while not kwargs["interrupt_check"]():
            time.sleep(0.01)
        raise engine.AgentInterrupted()

    thread, ws, _ = _start(env, body)
    _wait_for(lambda: cli.S.messages[-1:] == [{"role": "assistant", "content": "step 1 done"}])
    env.client.post("/api/sessions/resume", json={"name": "chat-b"})
    W._interrupt_turn_of(ws)   # the socket's Stop, while chat B is viewed
    thread.join(5)

    assert not thread.is_alive()
    assert [e["type"] for e in ws.events][-1] == "interrupted"
    assert [m["content"] for m in _file("chat-a")["messages"]] == ["do the task", "step 1 done"]
    assert [m["content"] for m in _file("chat-b")["messages"]] == ["b question", "b answer"]
    assert [m["content"] for m in cli.S.messages] == ["b question", "b answer"]


def test_a_failed_run_keeps_its_progress_in_its_chat(env):
    def body(messages, kwargs, engine):
        messages.append({"role": "assistant", "content": "partial"})
        raise RuntimeError("backend exploded")

    thread, ws, _ = _start(env, body)
    thread.join(5)
    assert [m["content"] for m in _file("chat-a")["messages"]] == ["do the task", "partial"]
    error = next(e for e in ws.events if e["type"] == "error")
    assert error["session_id"] == env.a_id


def test_the_running_chat_cannot_be_deleted_renamed_or_reset_but_others_can(env):
    gate, started = threading.Event(), threading.Event()
    thread, _ws, _ = _start(env, _gated_body(gate, started))
    assert started.wait(5)
    try:
        for method, path, body in [("delete", "/api/sessions/chat-a", None),
                                   ("patch", "/api/sessions/chat-a", {"new_name": "x"}),
                                   ("post", "/api/sessions/reset", {})]:
            response = env.client.request(method, path, json=body)
            assert response.status_code == 409, (path, response.text)
            assert "running a task" in response.json()["error"]
        # Compaction is a second model call: still refused during any run.
        assert env.client.post("/api/sessions/compact", json={}).status_code == 409
        assert cli._session_path("chat-a").exists()

        assert env.client.patch("/api/sessions/chat-b", json={"new_name": "chat-c"}).status_code == 200
        assert env.client.delete("/api/sessions/chat-c").status_code == 200
        assert not cli._session_path("chat-c").exists()
    finally:
        gate.set()
        thread.join(5)
    assert env.client.delete("/api/sessions/chat-a").status_code == 200


def test_a_second_task_waits_and_a_queued_prompt_runs_in_its_own_chat(env):
    gate, started = threading.Event(), threading.Event()
    thread, _ws, _ = _start(env, _gated_body(gate, started))
    assert started.wait(5)
    env.client.post("/api/sessions/resume", json={"name": "chat-b"})
    refused = _Socket()
    asyncio.run(W._handle_chat(refused, {"text": "second", "session_id": env.b_id},
                               _Engine(env.tmp, lambda *a: pytest.fail("ran")), cli))
    assert refused.events == [{"type": "error", "message": W.ANOTHER_TURN_RUNNING}]
    assert len(cli.S.messages) == 2
    gate.set()
    thread.join(5)

    # The browser's queue sends it once A is done, tagged with chat B's id,
    # and it runs in B even though the user is now viewing a third chat.
    env.client.post("/api/sessions/new", json={"name": ""})

    def answer(messages, kwargs, engine):
        messages.append({"role": "assistant", "content": "B second answer"})
        return "ok"

    thread, ws, _ = _start(env, answer, {"text": "second", "session_id": env.b_id})
    thread.join(5)
    assert [m["content"] for m in _file("chat-b")["messages"]][-2:] == ["second", "B second answer"]
    assert len(_file("chat-a")["messages"]) == 2
    assert cli.S.messages == []
    assert all(e["session_id"] == env.b_id for e in ws.events)


def test_old_session_files_load_and_gain_a_stable_id(env):
    old = cli._session_path("legacy")
    old.write_text(json.dumps({"version": 2, "name": "legacy",
                               "messages": [{"role": "user", "content": "old"}]}),
                   encoding="utf-8")
    listed = {s["name"]: s for s in env.client.get("/api/sessions").json()}
    assert listed["legacy"]["id"] is None
    assert listed["chat-a"]["id"] == env.a_id

    response = env.client.post("/api/sessions/resume", json={"name": "legacy"})
    assert response.status_code == 200, response.text
    sid = response.json()["id"]
    assert len(sid) == 32
    assert env.client.get("/api/history").json()["messages"] == [{"role": "user", "content": "old"}]

    W._save_session(cli, cli.S)
    W._save_session(cli, cli.S)
    data = _file("legacy")
    assert data["id"] == sid and data["source"] == "web" and data["version"] == 2
    assert data["created_at"] and data["updated_at"] and ":" in data["model"]


def test_session_file_writes_are_atomic(env, monkeypatch):
    before = cli._session_path("chat-b").read_bytes()

    def broken_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(W.json, "dump", broken_dump)
    with pytest.raises(OSError):
        W._save_session_file(cli._session_path("chat-b"), {"version": 2})
    assert cli._session_path("chat-b").read_bytes() == before
    assert [p.name for p in cli.SESSIONS_DIR.iterdir() if p.suffix == ".tmp"] == []
