"""CLI: agent8088 --logs [follow] [-n N] [--level L] [--subsystem S] [--json].

Reads the daily JSONL file directly. v1 has no RPC/remote tail — the file is
plain JSONL on disk so this is a tail -f with filtering.
"""
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent8088 import engine as A
from agent8088 import cli


def _seed_log(log_dir, n, *, levels=("INFO",), subsystems=("engine",)):
    """Write n JSONL records to today's file. Returns the file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    f = log_dir / f"agent8088-{today}.log"
    with f.open("a", encoding="utf-8") as fh:
        for i in range(n):
            level = levels[i % len(levels)]
            sub = subsystems[i % len(subsystems)]
            rec = {"ts": f"2026-08-20T12:{i:02d}:00+00:00", "level": level,
                   "subsystem": sub, "msg": f"record {i}"}
            fh.write(json.dumps(rec) + "\n")
    return f


def _make_args(log_dir, follow=False, limit=50, level=None, subsystem=None, json_out=False):
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return SimpleNamespace(
        logs=("follow" if follow else "tail"),
        log_dir=log_dir,
        log_file=log_dir / f"agent8088-{today}.log",
        limit=limit, level=level, subsystem=subsystem, json=json_out,
    )


def test_logs_prints_last_n_lines(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 100)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, limit=10))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    assert len(lines) == 10
    assert "record 99" in lines[-1]


def test_logs_level_filter(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 20, levels=("INFO", "WARNING"))
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, level="WARNING"))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    # Only WARNING lines should appear (every other record is WARNING)
    assert all("WARNING" in l for l in lines)
    assert len(lines) == 10


def test_logs_subsystem_filter(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 20, subsystems=("engine", "gateway"))
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, subsystem="gateway"))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    assert all("gateway" in l for l in lines)
    assert len(lines) == 10


def test_logs_json_emits_raw_jsonl(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 5)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir, limit=5, json_out=True))
    out, _ = capsys.readouterr()
    lines = [l for l in out.splitlines() if l.strip()]
    assert rc == 0
    assert len(lines) == 5
    # Each line must be valid JSON with the expected fields.
    for line in lines:
        obj = json.loads(line)
        assert {"ts", "level", "subsystem", "msg"} <= set(obj.keys())


def test_logs_missing_file_exits_clean(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"  # not seeded — no file exists
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    rc = cli.cmd_logs(_make_args(log_dir))
    out, _ = capsys.readouterr()
    assert rc == 1
    assert "No log file" in out
    assert "Traceback" not in out