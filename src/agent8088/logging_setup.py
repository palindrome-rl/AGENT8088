"""Operational log: one daily-rotating JSONL file with subsystem names.

The audit log (engine._audit) records permission decisions. This module is the
operational sink that `_log = logging.getLogger("agent8088.engine")` and every
sibling subsystem logger has been missing — they all had no configured sink.

Wires one custom DailyJsonlHandler onto the `agent8088` parent logger; every
child logger (engine, gateway, memory, mcp, gateway.platforms.*) inherits via
stdlib propagation. Never raises: a broken sink degrades to a no-op so it can
never break an agent turn (parity with the audit log contract, engine.py:5624).
"""
import json
import logging
import sys
from datetime import datetime, timezone

from agent8088 import engine as A


def _subsystem(name: str) -> str:
    """`agent8088.gateway.platforms.slack` -> `gateway/platforms/slack`."""
    if name.startswith("agent8088."):
        name = name[len("agent8088."):]
    return name.replace(".", "/")


class DailyJsonlHandler(logging.Handler):
    """One JSONL file per local day, named `agent8088-YYYY-MM-DD.log`.

    Opens the dated file on first emit and reopens a new dated file when the
    local date changes — produces the date-in-active-filename pattern from
    OpenClaw (`openclaw-2026-08-20.log`), which stdlib's TimedRotatingFileHandler
    does not (it keeps a base name and only suffixes on rotation).

    Never raises: emit() failures route through self.handleError(record),
    stdlib's non-raising error path.
    """

    def __init__(self, base_dir):
        super().__init__()
        self._base_dir = base_dir
        self._cur_date = None
        self._fh = None

    def _date_str(self):
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    def _open_for(self, date_str):
        path = self._base_dir / f"agent8088-{date_str}.log"
        existed = path.exists()
        self._fh = path.open("a", encoding="utf-8")
        if not existed:
            try:
                A._protect_private_file(path)
            except Exception:
                pass  # never let file protection break the sink
        self._cur_date = date_str

    def emit(self, record):
        try:
            today = self._date_str()
            if today != self._cur_date:
                if self._fh is not None:
                    self._fh.close()
                self._open_for(today)
            entry = {
                "ts": datetime.now(timezone.utc).astimezone().isoformat(),
                "level": record.levelname,
                "subsystem": _subsystem(record.name),
                "msg": A._redact_secrets(record.getMessage()),
            }
            self._fh.write(json.dumps(entry) + "\n")
            self._fh.flush()
        except Exception:
            self.handleError(record)  # stdlib: logs to stderr if enabled, never raises

    def close(self):
        try:
            if self._fh is not None:
                self._fh.close()
        finally:
            super().close()


def configure_logging() -> None:
    """Attach the DailyJsonlHandler to the `agent8088` parent logger. Idempotent.

    Never raises: on any setup failure (unwritable dir, permissions), logs once
    to stderr and returns with no handler attached — the agent runs normally
    and _log calls are no-ops as they were before.
    """
    try:
        if str(A.APP_CONFIG.get("log_enabled", "1")).strip() == "0":
            return
        level_name = str(A.APP_CONFIG.get("log_level", "INFO")).strip().upper()
        level = getattr(logging, level_name, logging.INFO)
        base_dir = A._agent_data_dir() / "logs"
        base_dir.mkdir(parents=True, exist_ok=True)
        parent = logging.getLogger("agent8088")
        # Idempotent: don't attach a second DailyJsonlHandler on repeat calls.
        if any(isinstance(h, DailyJsonlHandler) for h in parent.handlers):
            return
        parent.addHandler(DailyJsonlHandler(base_dir))
        parent.setLevel(level)
    except Exception as exc:
        print(f"[logging_setup] could not configure log file: {exc}", file=sys.stderr)


# ponytail: log_max_bytes is read nowhere — a single-day burst could grow the
# file unbounded until the day changes. Low risk for agent8088 (INFO-level agent
# events, not request-per-line HTTP). Upgrade path: size check in emit() that
# rolls mid-day. Tracked as a known ceiling.


def demo():
    """Ponytail self-check: emit a few records and print the file path."""
    configure_logging()
    log = logging.getLogger("agent8088.engine")
    log.info("demo: info record")
    log.warning("demo: warning record")
    logging.getLogger("agent8088.gateway").info("demo: gateway record")
    f = A._agent_data_dir() / "logs" / f"agent8088-{datetime.now().astimezone().strftime('%Y-%m-%d')}.log"
    print(f"log file: {f}")
    print(f.read_text(encoding="utf-8"))


if __name__ == "__main__":
    demo()