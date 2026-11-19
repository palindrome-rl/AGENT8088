"""Concurrent session mutation must not corrupt a running agent's trajectory.

The web bridge shares one Session singleton (cli.S) across all requests and
WebSockets. A turn runs run_agent(trajectory_state=S.trajectory_state) in a
thread, and TrajectoryState keeps a reference to that same dict. When another
request hit /api/sessions/new (auto-created by the web UI on a second tab's
first message) or /api/sessions/reset while a turn was in flight, those
endpoints mutated S.trajectory_state and S.messages IN PLACE, yanking the
contents out from under the running turn: after_tool crashed with
KeyError: 'checklist' (the checklist _normalise had installed was gone).

The endpoints now REPLACE the references (fresh objects) instead of clearing
in place, so a running turn keeps the objects it captured.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "home" / "config.txt"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    from agent8088 import web_server, cli
    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    monkeypatch.setattr(cli, 'SESSIONS_DIR', sessions)
    return TestClient(web_server.app)


def _cl():
    from agent8088 import web_server
    return web_server._cl()


def _simulate_running_turn(C):
    """What a run_agent thread captures when a turn starts."""
    from agent8088.trajectory import TrajectoryState
    trajectory = TrajectoryState(C.S.trajectory_state, "in-flight goal")
    messages = C.S.messages
    return trajectory, messages


def test_new_session_does_not_wipe_a_running_turns_trajectory(client):
    """Second tab's auto-created session must not clear the first tab's dict."""
    C = _cl()
    # Start a named session so new-session switching is the real flow.
    assert client.post("/api/sessions/new", json={"name": "tab-a"}).status_code == 200
    C.S.messages.append({"role": "user", "content": "in flight"})
    trajectory, messages = _simulate_running_turn(C)

    # Second tab sends its first message; the UI POSTs /api/sessions/new.
    response = client.post("/api/sessions/new", json={"name": "tab-b"})
    assert response.status_code == 200

    # The running turn's captured objects must be untouched.
    assert trajectory.data.get("checklist"), "checklist was yanked mid-turn"
    assert messages, "messages were yanked mid-turn"
    assert {"role": "user", "content": "in flight"} in messages
    # The session singleton points at fresh objects now.
    assert C.S.messages is not messages


def test_reset_session_does_not_wipe_a_running_turns_trajectory(client):
    C = _cl()
    assert client.post("/api/sessions/new", json={"name": "tab-a"}).status_code == 200
    C.S.messages.append({"role": "user", "content": "in flight"})
    trajectory, messages = _simulate_running_turn(C)

    assert client.post("/api/sessions/reset", json={}).status_code == 200

    assert trajectory.data.get("checklist"), "checklist was yanked mid-turn"
    assert messages, "messages were yanked mid-turn"
    assert C.S.messages is not messages


def test_resume_session_does_not_wipe_a_running_turns_trajectory(client):
    C = _cl()
    assert client.post("/api/sessions/new", json={"name": "tab-a"}).status_code == 200
    assert client.post("/api/sessions/new", json={"name": "tab-b"}).status_code == 200
    assert client.post("/api/sessions/resume", json={"name": "tab-a"}).status_code == 200
    C.S.messages.append({"role": "user", "content": "in flight"})
    trajectory, messages = _simulate_running_turn(C)

    # Switching to tab-b's session mid-turn replaces references, not contents.
    assert client.post("/api/sessions/resume", json={"name": "tab-b"}).status_code == 200

    assert trajectory.data.get("checklist"), "checklist was yanked mid-turn"
    assert messages, "messages were yanked mid-turn"
    assert C.S.messages is not messages


def test_delete_active_session_does_not_wipe_a_running_turns_trajectory(client):
    C = _cl()
    assert client.post("/api/sessions/new", json={"name": "tab-a"}).status_code == 200
    C.S.messages.append({"role": "user", "content": "in flight"})
    trajectory, messages = _simulate_running_turn(C)

    assert client.delete("/api/sessions/tab-a").status_code == 200

    assert trajectory.data.get("checklist"), "checklist was yanked mid-turn"
    assert messages, "messages were yanked mid-turn"
    assert C.S.messages is not messages