"""A small command-line task tracker that persists tasks to a local JSON file.

Each task is a dict with the following fields:
    id          int    Auto-incremented identifier (starts at 1).
    title       str    Short, non-empty name for the task.
    description str   Optional longer text (defaults to "").
    priority    str    One of "low", "medium", "high" (defaults to "medium").
    status      str    "pending" or "completed" (defaults to "pending").
    created_at  str    ISO-8601 timestamp set when the task is created.

Usage (module demo):
    python task_manager.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

VALID_PRIORITIES = {"low", "medium", "high"}
VALID_STATUSES = {"pending", "completed"}


class TaskManager:
    """Manage tasks backed by a JSON file."""

    def __init__(self, filepath: str = "tasks.json") -> None:
        self.filepath = filepath
        self.tasks: List[Dict[str, Any]] = []
        self._next_id = 1
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """Load tasks from the JSON file, or start empty if it doesn't exist."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.tasks = data.get("tasks", [])
                self._next_id = data.get("next_id", 1)
            except (json.JSONDecodeError, OSError):
                self.tasks = []
                self._next_id = 1
        else:
            self.tasks = []
            self._next_id = 1

        # Defensive: make sure next_id is ahead of all existing ids.
        if self.tasks:
            self._next_id = max(self._next_id,
                                max(t["id"] for t in self.tasks) + 1)

    def _save(self) -> None:
        """Persist tasks and next_id to the JSON file (atomic write)."""
        payload = {"tasks": self.tasks, "next_id": self._next_id}
        tmp = self.filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.filepath)

    # ------------------------------------------------------------------ #
    # CRUD operations
    # ------------------------------------------------------------------ #
    def create_task(self, title: str, description: str = "",
                    priority: str = "medium") -> Dict[str, Any]:
        """Create a new task and return it."""
        if not title or not title.strip():
            raise ValueError("title must be a non-empty string")
        if priority not in VALID_PRIORITIES:
            raise ValueError(
                f"priority must be one of {sorted(VALID_PRIORITIES)}")

        task = {
            "id": self._next_id,
            "title": title.strip(),
            "description": description,
            "priority": priority,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        self._next_id += 1
        self.tasks.append(task)
        self._save()
        return task

    def edit_task(self, task_id: int, title: Optional[str] = None,
                  description: Optional[str] = None,
                  priority: Optional[str] = None) -> Dict[str, Any]:
        """Edit an existing task. Only provided fields are updated."""
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"task with id {task_id} not found")

        if title is not None:
            if not title.strip():
                raise ValueError("title must be a non-empty string")
            task["title"] = title.strip()
        if description is not None:
            task["description"] = description
        if priority is not None:
            if priority not in VALID_PRIORITIES:
                raise ValueError(
                    f"priority must be one of {sorted(VALID_PRIORITIES)}")
            task["priority"] = priority

        self._save()
        return task

    def complete_task(self, task_id: int) -> Dict[str, Any]:
        """Mark a task as completed."""
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"task with id {task_id} not found")
        task["status"] = "completed"
        self._save()
        return task

    def delete_task(self, task_id: int) -> Dict[str, Any]:
        """Delete a task and return the deleted task."""
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"task with id {task_id} not found")
        self.tasks.remove(task)
        self._save()
        return task

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Return the task with the given id, or None."""
        for task in self.tasks:
            if task["id"] == task_id:
                return task
        return None

    def list_tasks(self) -> List[Dict[str, Any]]:
        """Return all tasks (in creation order)."""
        return list(self.tasks)

    def search_tasks(self, query: str) -> List[Dict[str, Any]]:
        """Case-insensitive substring search over title and description."""
        if not query:
            return []
        q = query.lower()
        return [
            t for t in self.tasks
            if q in t["title"].lower() or q in t["description"].lower()
        ]


# ---------------------------------------------------------------------- #
# Demo entry point
# ---------------------------------------------------------------------- #
def _demo() -> None:
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), "demo_tasks.json")
    if os.path.exists(tmp):
        os.remove(tmp)

    tm = TaskManager(tmp)
    t1 = tm.create_task("Buy groceries", "Milk, eggs, bread", "high")
    t2 = tm.create_task("Write report", "Quarterly financial summary", "medium")
    t3 = tm.create_task("Call mom", "", "low")
    print("Created:", t1["id"], t2["id"], t3["id"])

    tm.edit_task(t2["id"], title="Write Q3 report",
                 description="Quarterly financial summary for Q3")
    tm.complete_task(t1["id"])
    tm.delete_task(t3["id"])

    print("Search 'report':", [t["title"] for t in tm.search_tasks("report")])

    # Reload from disk to verify persistence.
    tm2 = TaskManager(tmp)
    print("Reloaded tasks:", [t["title"] for t in tm2.list_tasks()])
    print("Persisted completed:",
          tm2.get_task(t1["id"])["status"] == "completed")

    os.remove(tmp)


if __name__ == "__main__":
    _demo()