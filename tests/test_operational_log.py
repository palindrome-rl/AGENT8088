"""Operational log: a daily-rotating JSONL file with subsystem names.

The audit log (test_audit_log.py) records permission decisions. This file tests
the operational log that records what the agent *did* and what happened to it
while running — the sink that `_log = logging.getLogger("agent8088.engine")` and
every sibling subsystem logger currently lacks.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from agent8088 import engine as A
from agent8088 import logging_setup as L


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Point _agent_data_dir at a temp dir so logs land in tmp_path/logs."""
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    # Reset the agent8088 parent logger between tests so handlers don't accumulate.
    parent = logging.getLogger("agent8088")
    parent.handlers = [h for h in parent.handlers if not isinstance(h, L.DailyJsonlHandler)]
    return tmp_path / "logs"


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _today_file(log_dir):
    return log_dir / f"agent8088-{datetime.now().astimezone().strftime('%Y-%m-%d')}.log"


def test_configure_logging_writes_jsonl_to_log_dir(log_dir):
    L.configure_logging()
    logging.getLogger("agent8088.engine").info("hello world")
    f = _today_file(log_dir)
    assert f.exists(), f"no log file at {f}"
    entries = _read_jsonl(f)
    assert len(entries) == 1
    e = entries[0]
    assert e["level"] == "INFO"
    assert e["subsystem"] == "engine"
    assert e["msg"] == "hello world"
    assert "ts" in e
    datetime.fromisoformat(e["ts"])  # raises if not ISO


def test_subsystem_name_strips_agent8088_prefix(log_dir):
    L.configure_logging()
    logging.getLogger("agent8088.gateway.platforms.slack").warning("connect")
    entries = _read_jsonl(_today_file(log_dir))
    assert entries[0]["subsystem"] == "gateway/platforms/slack"


def test_secrets_redacted_in_log_record(log_dir, monkeypatch):
    monkeypatch.setattr(A, "_SECRET_VALUES", ["sk-live-abcdef0123456789"])
    L.configure_logging()
    logging.getLogger("agent8088.engine").warning("got key=sk-live-abcdef0123456789")
    text = _today_file(log_dir).read_text(encoding="utf-8")
    assert "sk-live-abcdef0123456789" not in text
    assert "[redacted]" in text


def test_log_enabled_zero_writes_no_file(log_dir, monkeypatch):
    monkeypatch.setattr(A, "APP_CONFIG", {**A.APP_CONFIG, "log_enabled": "0"})
    parent = logging.getLogger("agent8088")
    parent.handlers = [h for h in parent.handlers if not isinstance(h, L.DailyJsonlHandler)]
    L.configure_logging()
    logging.getLogger("agent8088.engine").info("should vanish")
    assert not _today_file(log_dir).exists()


def test_unwritable_log_dir_does_not_raise(tmp_path, monkeypatch):
    """A broken sink must not break the agent turn (parity with audit log)."""
    # Point _agent_data_dir at a path whose logs/ subdir cannot be created.
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path / "readonly")
    # Make the parent read-only so mkdir fails. On Windows, chmod is advisory,
    # so also monkeypatch Path.mkdir to raise — the point is the never-raise,
    # not the specific failure mode.
    def _boom(*a, **kw):
        raise OSError("read-only filesystem")
    monkeypatch.setattr(Path, "mkdir", _boom)
    # Must not raise:
    L.configure_logging()
    # And the agent logger must have no DailyJsonlHandler attached:
    assert not any(isinstance(h, L.DailyJsonlHandler)
                   for h in logging.getLogger("agent8088").handlers)


def test_daily_rotation_opens_new_dated_file(log_dir, monkeypatch):
    """When the local date changes, the handler opens a new dated file."""
    L.configure_logging()
    # Emit a record for "today"
    logging.getLogger("agent8088.engine").info("day one")
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    # Force the handler to think it's tomorrow by patching datetime in the module
    tomorrow_dt = (datetime.now().astimezone() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return tomorrow_dt if tz is timezone.utc else tomorrow_dt.astimezone()
        @staticmethod
        def fromisoformat(s):
            return datetime.fromisoformat(s)
    monkeypatch.setattr(L, "datetime", _FakeDT)
    # Emit again — handler should detect day change and open a new file
    logging.getLogger("agent8088.engine").info("day two")
    tomorrow = tomorrow_dt.strftime("%Y-%m-%d")
    f_tomorrow = log_dir / f"agent8088-{tomorrow}.log"
    assert f_tomorrow.exists(), f"expected new file {f_tomorrow}"
    entries = _read_jsonl(f_tomorrow)
    assert entries[0]["msg"] == "day two"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_log_file_is_private(log_dir):
    """Operational log file gets 0600 on POSIX, same as the audit log."""
    L.configure_logging()
    logging.getLogger("agent8088.engine").info("x")
    assert oct(_today_file(log_dir).stat().st_mode)[-3:] == "600"


def test_main_calls_configure_logging(tmp_path, monkeypatch):
    """cli.main() must configure logging before dispatching to any mode."""
    called = []
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    import agent8088.cli as cli
    monkeypatch.setattr(cli, "configure_logging", lambda: called.append(True))
    monkeypatch.setattr(sys, "argv", ["agent8088", "--version"])
    with pytest.raises(SystemExit):
        cli.main()
    assert called, "configure_logging was not called from main()"


def test_mcp_server_entrypoint_uses_configure_logging():
    """`python -m agent8088.mcp_server` bypasses cli.main(); its entry function
    must wire the daily JSONL sink via configure_logging() rather than the
    stderr-only logging.basicConfig. Source contract test — the MCP server does
    heavy stdio/HTTP work that's not deterministic to drive without a real
    MCP transport, so we assert the wiring exists, not the behavior."""
    from pathlib import Path
    import agent8088.mcp_server as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "logging.basicConfig" not in src, (
        "mcp_server.py still uses logging.basicConfig; the MCP entrypoint must "
        "call configure_logging() so the agent8088.mcp_server logger gets the "
        "daily JSONL file sink (parity with gateway/__main__.py)."
    )
    assert "configure_logging" in src, (
        "mcp_server.py must import and call configure_logging from "
        "logging_setup to attach the operational log sink."
    )