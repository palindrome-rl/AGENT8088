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
        self._closed = False

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
        if self._closed:
            # logging.shutdown() already ran: this is interpreter teardown
            # (asyncio reporting a library's orphaned task). Writing would
            # raise on the closed file and print a "Logging error" traceback.
            return
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
        self._closed = True
        try:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
        finally:
            super().close()


class DegradedConsoleHandler(logging.Handler):
    """Echo degradation events to stderr — and nothing else.

    Ordinary warnings stay file-only (that is the point of the JSONL sink), but
    "web search fell back to a keyless scraper" is something the operator has
    to see, and before this handler it only ever reached the log file.

    Emits only records flagged ``degraded=True`` (capabilities.report() sets it
    on every state change it logs; any subsystem may pass
    ``extra={"degraded": True}`` too) at WARNING or above. Recoveries are INFO,
    so they stay in the file here: a UI that wants "upgraded back" notices
    subscribes to capabilities instead.

    Steps aside when a UI is listening: capabilities passes ``console=False``
    when it has subscribers (the REPL renders the notice itself at a safe point,
    where a raw stderr write would tear a spinner or a live render). Without a
    subscriber — gateway, MCP server, web server, --doctor, headless runs — this
    is the only place the notice surfaces. Resolves sys.stderr per emit so a
    redirected stream (pytest, a daemon) is honoured. Never raises.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)

    def filter(self, record):
        if record.levelno < self.level or not getattr(record, "degraded", False):
            return False
        console = getattr(record, "console", None)
        if console is None:
            from agent8088 import capabilities
            console = not capabilities.has_subscribers()
        return bool(console)

    def emit(self, record):
        try:
            sys.stderr.write(f"agent8088 \u26a0 {A._redact_secrets(record.getMessage())}\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


def _install_degraded_console(parent) -> None:
    """Attach the DegradedConsoleHandler once. Independent of log_enabled: a
    user who turned the log file off still needs to hear about a fallback."""
    from agent8088 import capabilities
    # Its own level, so a log_level=ERROR file setting cannot swallow the
    # WARNING-level degradation events before any handler sees them.
    logging.getLogger(capabilities.LOGGER_NAME).setLevel(logging.INFO)
    if not any(isinstance(h, DegradedConsoleHandler) for h in parent.handlers):
        parent.addHandler(DegradedConsoleHandler())


def configure_logging() -> None:
    """Attach the DailyJsonlHandler to the `agent8088` parent logger. Idempotent.

    Never raises: on any setup failure (unwritable dir, permissions), logs once
    to stderr and returns with no handler attached — the agent runs normally
    and _log calls are no-ops as they were before.
    """
    try:
        _install_degraded_console(logging.getLogger("agent8088"))
    except Exception as exc:  # noqa: BLE001
        print(f"[logging_setup] could not attach degradation notices: {exc}", file=sys.stderr)
    try:
        if str(A.APP_CONFIG.get("log_enabled", "1")).strip() == "0":
            return
        level_name = str(A.APP_CONFIG.get("log_level", "INFO")).strip().upper()
        level = getattr(logging, level_name, logging.INFO)
        base_dir = A._agent_data_dir() / "logs"
        base_dir.mkdir(parents=True, exist_ok=True)
        parent = logging.getLogger("agent8088")
        # browser-use configures a console handler on the root logger. Keep
        # agent8088 records in the JSONL sink instead of duplicating them there.
        parent.propagate = False
        # Idempotent: don't attach a second DailyJsonlHandler on repeat calls.
        if any(isinstance(h, DailyJsonlHandler) for h in parent.handlers):
            return
        handler = DailyJsonlHandler(base_dir)
        parent.addHandler(handler)
        parent.setLevel(level)
        # asyncio reports orphaned tasks from libraries (Playwright's sync
        # driver: "Task was destroyed but it is pending!") through its own
        # logger, which falls through to stderr and lands under the prompt at
        # exit. Keep those in the log file, where a bug report can find them.
        asyncio_logger = logging.getLogger("asyncio")
        if not any(isinstance(h, DailyJsonlHandler) for h in asyncio_logger.handlers):
            asyncio_logger.addHandler(handler)
            asyncio_logger.propagate = False
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
