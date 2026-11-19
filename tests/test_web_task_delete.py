"""Deleting a durable task: DELETE /api/tasks/{id} and `/task delete <id>`.

QA: the Tasks page could only end (cancel) a task; nothing removed one.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from agent8088.task_runtime import TaskStore


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "home" / "config.txt"))
    (tmp_path / "home").mkdir()
    from agent8088 import web_server
    path = tmp_path / "tasks.db"
    monkeypatch.setitem(web_server._eng().APP_CONFIG, "task_db_path", str(path))
    store = TaskStore(path)
    yield TestClient(web_server.app), store, path
    store.close()


def _task(store, state):
    task_id = store.create(state, ".", [{"role": "user", "content": state}])
    store.finish_operation(store.start_operation(task_id, "read_text", {"filename": "a"}), "ok")
    store.update(task_id, state=state)
    return task_id


def _rows(path, task_id):
    """Row counts left in tasks.db for one task, read through a fresh connection."""
    with closing(sqlite3.connect(path)) as db:
        return [db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (task_id,)).fetchone()[0]
                for table, column in (("tasks", "id"), ("task_events", "task_id"),
                                      ("task_operations", "task_id"))]


@pytest.mark.parametrize("state", ["paused", "completed", "cancelled"])
def test_delete_removes_the_task_and_its_ledger_only(env, state):
    client, store, path = env
    task_id, other = _task(store, state), _task(store, "paused")
    assert all(_rows(path, task_id))

    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert _rows(path, task_id) == [0, 0, 0]
    assert all(_rows(path, other))
    assert client.get(f"/api/tasks/{task_id}").status_code == 404
    assert [task["id"] for task in client.get("/api/tasks?include_cancelled=true").json()] == [other]


def test_unknown_task_is_not_found(env):
    client, _store, _path = env
    response = client.delete("/api/tasks/" + "0" * 32)
    assert response.status_code == 404
    assert "error" in response.json()


@pytest.mark.parametrize("state", ["running", "queued"])
def test_live_task_is_a_conflict_and_is_kept(env, state):
    client, store, path = env
    task_id = _task(store, state)

    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 409
    assert "end the task first" in response.json()["error"]
    assert all(_rows(path, task_id))
    assert store.get(task_id)["state"] == state


@pytest.mark.parametrize("ref", ["%25", "_", "not-a-task"])
def test_invalid_id_is_rejected_and_deletes_nothing(env, ref):
    """`%` and `_` are LIKE wildcards; they must not resolve to some task."""
    client, store, path = env
    task_id = _task(store, "paused")

    response = client.delete(f"/api/tasks/{ref}")

    assert response.status_code == 404
    assert all(_rows(path, task_id))


def test_cli_task_delete_uses_the_short_id_and_refuses_a_live_task(env):
    from agent8088 import cli
    _client, store, path = env
    done, live = _task(store, "completed"), _task(store, "running")

    cli.cmd_task(f"delete {done[:12]}")
    cli.cmd_task(f"delete {live[:12]}")

    assert _rows(path, done) == [0, 0, 0]
    assert all(_rows(path, live))
