"""Durable review records, private to the user's data directory.

`OutputStore` holds forty entries in memory and is gone at exit, which is right
for a tool result and wrong for a review: resume, history and the Web UI all
need the findings to outlive the turn that produced them. SQLite rather than a
directory of JSON because a review is read by id and listed by recency, and
because two processes -- the CLI and the Web UI -- can hold it open at once.

Findings are stored exactly as normalised. Nothing here re-validates them; a
stored `position_valid` was true when the review ran and says nothing about the
file now, which is why `reopen` recomputes it rather than trusting the record.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

SCHEMA_VERSION = 1
MAX_REVIEWS = 200


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS reviews ("
        " id TEXT PRIMARY KEY, created REAL NOT NULL, repo TEXT NOT NULL,"
        " mode TEXT NOT NULL, engine_version TEXT NOT NULL, status TEXT NOT NULL,"
        " findings INTEGER NOT NULL, payload TEXT NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS reviews_created ON reviews(created DESC)")
    return connection


def _private(path: Path) -> None:
    """A review quotes source lines, so the file is as sensitive as the code."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def save(store_path, repo, result: dict) -> str:
    """Record one review and return its id. Never raises on a full disk."""
    review_id = "rev-" + uuid.uuid4().hex[:12]
    path = Path(store_path)
    try:
        with closing(_connect(path)) as connection, connection:
            connection.execute(
                "INSERT INTO reviews (id, created, repo, mode, engine_version, status,"
                " findings, payload) VALUES (?,?,?,?,?,?,?,?)",
                (review_id, time.time(), str(repo), str(result.get("mode") or "unknown"),
                 str(result.get("engine_version") or ""), str(result.get("status") or ""),
                 len(result.get("findings") or []),
                 json.dumps(result, ensure_ascii=False)))
            # Bounded on purpose: this is a working history, not an audit log.
            connection.execute(
                "DELETE FROM reviews WHERE id NOT IN"
                " (SELECT id FROM reviews ORDER BY created DESC LIMIT ?)", (MAX_REVIEWS,))
        _private(path)
    except (sqlite3.Error, OSError, ValueError):
        return ""
    return review_id


def load(store_path, review_id: str) -> dict | None:
    if not isinstance(review_id, str) or not review_id.startswith("rev-"):
        return None
    try:
        with closing(_connect(Path(store_path))) as connection:
            row = connection.execute(
                "SELECT payload, repo FROM reviews WHERE id = ?", (review_id,)).fetchone()
    except (sqlite3.Error, OSError):
        return None
    if not row:
        return None
    try:
        payload = json.loads(row[0])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    payload["repository"] = row[1]
    payload["review_id"] = review_id
    return payload


def recent(store_path, limit: int = 20) -> list:
    try:
        with closing(_connect(Path(store_path))) as connection:
            rows = connection.execute(
                "SELECT id, created, repo, mode, engine_version, status, findings"
                " FROM reviews ORDER BY created DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    except (sqlite3.Error, OSError):
        return []
    return [{"id": r[0], "created": r[1], "repo": r[2], "mode": r[3],
             "engine_version": r[4], "status": r[5], "findings": r[6]} for r in rows]


def reopen(store_path, review_id: str, root, *, recheck) -> dict | None:
    """Load a review and re-establish whether each finding still applies.

    A stored `position_valid` was true when the review ran. Between then and now
    the file may have been edited, reverted or deleted, and acting on the stale
    answer is exactly the stale-diff failure the design note asks to prevent. So
    the stored value is replaced, never trusted, and a finding that no longer
    matches is marked rather than dropped -- the reviewer still said something
    about that file, and silently losing it would be worse than saying it moved.

    `recheck` answers True/False, or the (start_line, end_line) the quoted code
    sits at now. When that differs from the stored lines the finding is moved
    there and remembers where the review put it: a fix has to land on the code,
    not on whatever now occupies the old line number.
    """
    payload = load(store_path, review_id)
    if payload is None:
        return None
    refreshed = []
    for finding in payload.get("findings") or []:
        current = dict(finding)
        answer = recheck(root, finding)
        current["position_valid"] = bool(answer)
        if isinstance(answer, tuple) and len(answer) == 2:
            start, end = answer
            stored = finding.get("start_line")
            if (start, end) != (stored, finding.get("end_line") or stored):
                current["start_line"], current["end_line"] = start, end
                current["moved_from"] = stored
                current["verification"] = "moved"
        if finding.get("position_valid") and not current["position_valid"]:
            current["verification"] = "stale"
        refreshed.append(current)
    payload["findings"] = refreshed
    payload["reopened"] = True
    return payload
