"""Small, restart-safe runtime for work that outlives one model turn."""

from __future__ import annotations

import contextvars
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


_ACTIVE = contextvars.ContextVar("agent8088_task_runtime", default=None)
_SECRET_WORDS = ("key", "token", "secret", "password", "authorization")


def _safe_json(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if any(w in k.lower() for w in _SECRET_WORDS)
                else _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v) for v in value]
    return value


def _now() -> float:
    return time.time()


class TaskStore:
    """SQLite store with atomic checkpoints and an append-only operation ledger."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, goal TEXT NOT NULL, workspace TEXT NOT NULL,
          state TEXT NOT NULL, slice_no INTEGER NOT NULL DEFAULT 0,
          messages_json TEXT NOT NULL, last_answer TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
          kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_operations (
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool TEXT NOT NULL,
          args_json TEXT NOT NULL, state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
          started_at REAL NOT NULL, finished_at REAL
        );
        CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task_id, id);
        CREATE INDEX IF NOT EXISTS task_operations_task ON task_operations(task_id, started_at);
        """)
        self.db.commit()

    def create(self, goal: str, workspace: str | Path, messages: list[dict]) -> str:
        task_id = uuid.uuid4().hex
        now = _now()
        self.db.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, 'queued', 0, ?, '', '', ?, ?)",
            (task_id, goal, str(workspace), json.dumps(messages), now, now),
        )
        self.event(task_id, "created", {"goal": goal})
        self.db.commit()
        return task_id

    def get(self, task_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM tasks WHERE state != 'cancelled' ORDER BY updated_at DESC").fetchall()]

    def resolve(self, task_ref: str) -> dict:
        """Find one task by its full id or the short id shown by `/task list`."""
        task = self.get(task_ref)
        if task:
            return task
        rows = self.db.execute("SELECT * FROM tasks WHERE id LIKE ?", (f"{task_ref}%",)).fetchall()
        if len(rows) != 1:
            raise KeyError(task_ref)
        return dict(rows[0])

    def recent_operations(self, task_id: str, limit: int = 12) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM task_operations WHERE task_id=? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def cancel(self, task_id: str) -> dict:
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task["state"] == "completed":
            return task
        self.db.execute(
            "UPDATE tasks SET state='cancelled', error='ended by user', updated_at=? WHERE id=?",
            (_now(), task_id),
        )
        self.event(task_id, "cancelled", {})
        self.db.commit()
        return self.get(task_id)

    def update(self, task_id: str, **fields) -> None:
        allowed = {"state", "slice_no", "messages_json", "last_answer", "error"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        fields["updated_at"] = _now()
        sql = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE tasks SET {sql} WHERE id=?", (*fields.values(), task_id))
        self.db.commit()

    def checkpoint(self, task_id: str, *, messages=None, answer=None, state=None, error=None):
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        self.update(
            task_id,
            messages_json=json.dumps(messages if messages is not None
                                     else json.loads(task["messages_json"])),
            last_answer=str(answer if answer is not None else task["last_answer"])[-6000:],
            state=state or task["state"],
            error=str(error or ""),
        )

    def event(self, task_id: str, kind: str, payload: dict) -> None:
        self.db.execute(
            "INSERT INTO task_events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)",
            (task_id, kind, json.dumps(_safe_json(payload), default=str), _now()),
        )

    def start_operation(self, task_id: str, tool: str, args: dict) -> str:
        op_id = uuid.uuid4().hex
        now = _now()
        self.db.execute(
            "INSERT INTO task_operations VALUES(?,?,?,?,?,?,?,NULL)",
            (op_id, task_id, tool, json.dumps(_safe_json(args), default=str),
             "started", "", now),
        )
        self.event(task_id, "operation_intent", {"operation_id": op_id, "tool": tool})
        self.db.commit()
        return op_id

    def finish_operation(self, op_id: str, result: str, state: str = "completed") -> None:
        self.db.execute(
            "UPDATE task_operations SET state=?, result=?, finished_at=? WHERE id=?",
            (state, str(result)[-8000:], _now(), op_id),
        )
        row = self.db.execute("SELECT task_id,tool FROM task_operations WHERE id=?", (op_id,)).fetchone()
        if row:
            self.event(row["task_id"], "operation_result", {
                "operation_id": op_id, "tool": row["tool"], "state": state,
            })
        self.db.commit()

    def recover(self, task_id: str) -> int:
        """Mark one task interrupted by a process death as resumable."""
        cur = self.db.execute(
            "UPDATE task_operations SET state='unknown', finished_at=? "
            "WHERE task_id=? AND state='started'", (_now(), task_id),
        )
        self.db.execute(
            "UPDATE tasks SET state='paused', error='process interrupted', updated_at=? "
            "WHERE id=? AND state='running'", (_now(), task_id),
        )
        self.db.commit()
        return cur.rowcount

    def close(self):
        self.db.close()


class TaskRuntime:
    def __init__(self, store: TaskStore, task_id: str):
        self.store, self.task_id = store, task_id
        self.messages: list[dict] | None = None

    def bind(self, messages: list[dict]):
        self.messages = messages

    def before_tool(self, name: str, args: dict) -> str:
        return self.store.start_operation(self.task_id, name, args)

    def after_tool(self, operation_id: str, result: str):
        self.store.finish_operation(operation_id, result,
                                    "blocked" if str(result).startswith("ESCALATION_REQUEST")
                                    else "completed")
        if self.messages is not None:
            self.store.checkpoint(self.task_id, messages=_compact(self.messages))

    @contextmanager
    def active(self):
        token = _ACTIVE.set(self)
        try:
            yield self
        finally:
            _ACTIVE.reset(token)


def current_runtime() -> TaskRuntime | None:
    return _ACTIVE.get()


def _compact(messages: list[dict], keep: int = 10) -> list[dict]:
    if len(messages) <= keep + 2:
        return messages
    return [*messages[:2], {
        "role": "user",
        "content": "Earlier task context was checkpointed; continue from the operation ledger.",
    }, *messages[-keep:]]


def store_path(config_path: str | Path) -> Path:
    config = Path(config_path).expanduser()
    return config.with_name("tasks.db")


def run_task(goal: str, agent: Callable, *, store: TaskStore, workspace: str | Path,
             task_id: str | None = None, max_slices: int = 8, slice_turns: int = 8,
             verify: Callable[[dict, str], bool | tuple[bool, str]] | None = None,
             on_slice: Callable[[dict, str], None] | None = None,
             **agent_kwargs) -> dict:
    """Run bounded model slices, checkpointing after every tool and slice.

    ``verify`` is deliberately explicit: a model's prose is not proof that work exists.
    """
    if task_id:
        task = store.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task["state"] in {"completed", "cancelled"}:
            return task
        store.recover(task_id)
        task = store.get(task_id)
        # The model API receives its system prompt separately. Drop any stale
        # in-band system messages from an interrupted pre-runtime run.
        messages = [m for m in json.loads(task["messages_json"])
                    if m.get("role") != "system"]
    else:
        messages = [{"role": "user", "content":
                     "This is a durable task. Work in small, concrete tool steps. "
                     "Checkpointed progress survives a restart. Do not claim completion "
                     "until the requested verification has actually passed. End a verified "
                     "final answer with TASK_COMPLETE; otherwise end it with TASK_PROGRESS.\n\n"
                     + goal}]
        task_id = store.create(goal, workspace, messages)
    runtime = TaskRuntime(store, task_id)
    for _ in range(max_slices):
        task = store.get(task_id)
        if task["state"] == "cancelled":
            break
        store.update(task_id, state="running", slice_no=int(task["slice_no"]) + 1, error="")
        task = store.get(task_id)
        if on_slice:
            on_slice(task, "running")
        runtime.bind(messages)
        try:
            with runtime.active():
                answer = agent(messages, max_turns=slice_turns, **agent_kwargs)
        except Exception as exc:
            store.checkpoint(task_id, messages=_compact(messages), state="paused", error=str(exc))
            break
        store.checkpoint(task_id, messages=_compact(messages), answer=answer)
        task = store.get(task_id)
        if task["state"] == "cancelled":
            break
        if on_slice:
            on_slice(task, "checkpointed")
        if verify:
            try:
                verdict = verify(task, answer)
            except Exception as exc:
                verdict = (False, f"Verifier raised {type(exc).__name__}; inspect the artifacts and retry.")
        else:
            verdict = False
        verified = verdict[0] if isinstance(verdict, tuple) else verdict
        feedback = verdict[1] if isinstance(verdict, tuple) and len(verdict) > 1 else ""
        if verify and verified:
            store.update(task_id, state="completed", error="")
            store.event(task_id, "verified", {})
            break
        if not verify and "TASK_COMPLETE" in str(answer):
            store.update(task_id, state="completed")
            break
        messages = _compact(messages)
        messages.append({"role": "user", "content":
            "Continue this durable task from the checkpoint. Do one concrete next action "
            "with tools. Report TASK_COMPLETE if the task is now fully complete and "
            "verified; otherwise report TASK_PROGRESS. Never claim completion without proof."
            + (f"\nVerifier feedback: {feedback}" if feedback else "")})
        store.checkpoint(task_id, messages=_compact(messages), state="queued")
    else:
        store.update(task_id, state="paused")
    task = store.get(task_id)
    if on_slice:
        on_slice(task, task["state"])
    return task
