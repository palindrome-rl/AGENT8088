"""JSON persistence layer with graceful error handling."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


class Database:
    """A simple JSON-backed database.

    Stores three top-level keys: ``rooms``, ``customers``, ``bookings``.
    Handles missing files (returns empty state) and corrupted files
    (logs a warning and returns empty state) gracefully.
    """

    def __init__(self, file_path: str = "hotel_db.json"):
        self.file_path = file_path
        self.data: dict[str, Any] = {"rooms": [], "customers": [], "bookings": []}
        self.load()

    def load(self) -> None:
        """Load data from the JSON file.

        - Missing file → start with empty state (no error).
        - Corrupted JSON → log a warning and start with empty state.
        - Valid file → populate ``self.data``.
        """
        if not os.path.exists(self.file_path):
            # No file yet — start fresh.
            self.data = {"rooms": [], "customers": [], "bookings": []}
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, IOError) as exc:
            # Corrupted or unreadable file — degrade gracefully.
            print(f"[WARNING] Could not load {self.file_path}: {exc}. Starting with empty database.")
            self.data = {"rooms": [], "customers": [], "bookings": []}
            return

        # Ensure all expected keys exist (partial / legacy data).
        self.data = {
            "rooms": raw.get("rooms", []) if isinstance(raw, dict) else [],
            "customers": raw.get("customers", []) if isinstance(raw, dict) else [],
            "bookings": raw.get("bookings", []) if isinstance(raw, dict) else [],
        }

    def save(self) -> None:
        """Atomically save data to the JSON file.

        Writes to a temporary file first, then renames it to the target
        path to reduce the risk of corruption on crash.
        """
        dir_name = os.path.dirname(os.path.abspath(self.file_path))
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            # On Windows, target must not exist for os.replace to work.
            if os.path.exists(self.file_path):
                os.remove(self.file_path)
            os.rename(tmp_path, self.file_path)
        except Exception:
            # Clean up temp file on failure.
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    # ------------------------------------------------------------------ #
    # Convenience accessors
    # ------------------------------------------------------------------ #

    @property
    def rooms(self) -> list[dict]:
        return self.data["rooms"]

    @property
    def customers(self) -> list[dict]:
        return self.data["customers"]

    @property
    def bookings(self) -> list[dict]:
        return self.data["bookings"]
