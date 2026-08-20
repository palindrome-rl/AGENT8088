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


def test_logs_follow_emits_new_lines(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    f = _seed_log(log_dir, 5)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    args = _make_args(log_dir, follow=True, limit=5)

    # Run cmd_logs in a thread; it blocks on follow. Stop it by setting a flag
    # the function polls. We monkeypatch time.sleep to set the stop flag after
    # the append so the test is deterministic — no real time.sleep in the path.
    stop = {"flag": False}
    appended = threading.Event()

    def _fake_sleep(_secs):
        if not appended.is_set():
            # First few sleeps happen before the append — append now.
            with f.open("a", encoding="utf-8") as fh:
                for i in range(3):
                    fh.write(json.dumps({"ts": "2026-08-20T13:00:00+00:00",
                                         "level": "INFO",
                                         "subsystem": "engine",
                                         "msg": f"new {i}"}) + "\n")
            appended.set()
            return  # return once so the loop reads the new bytes
        # After the append, signal stop on the next sleep.
        stop["flag"] = True
        raise KeyboardInterrupt  # how cmd_logs follow exits

    monkeypatch.setattr(time, "sleep", _fake_sleep)
    monkeypatch.setattr(A, "time", time)  # if engine imported time locally
    # Some cli code may import time itself; patch the module-level reference too.
    import agent8088.cli as _cli
    monkeypatch.setattr(_cli.time, "sleep", _fake_sleep, raising=False)

    rc = cli.cmd_logs(args)  # should exit via KeyboardInterrupt internally
    out, _ = capsys.readouterr()
    # The 5 seeded lines should print first, then the 3 new ones.
    assert "record 4" in out  # last seeded line
    assert "new 0" in out and "new 1" in out and "new 2" in out


def test_logs_follow_handles_rotation(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    f = _seed_log(log_dir, 3)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    args = _make_args(log_dir, follow=True, limit=3)

    rotated = threading.Event()

    def _fake_sleep(_secs):
        if not rotated.is_set():
            # Simulate rotation: move the current file aside, write a fresh file.
            rotated_path = f.with_suffix(f.suffix + ".old")
            f.replace(rotated_path)
            with f.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": "2026-08-20T14:00:00+00:00",
                                     "level": "INFO",
                                     "subsystem": "engine",
                                     "msg": "post-rotation"}) + "\n")
            rotated.set()
            return  # return once so the loop detects the rotation
        if rotated.is_set():
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", _fake_sleep)
    import agent8088.cli as _cli
    monkeypatch.setattr(_cli.time, "sleep", _fake_sleep, raising=False)

    cli.cmd_logs(args)
    out, _ = capsys.readouterr()
    assert "Log cursor reset (file rotated)." in out
    assert "post-rotation" in out


def test_main_logs_flag_dispatches_to_cmd_logs(tmp_path, monkeypatch, capsys):
    """agent8088 --logs must dispatch to cmd_logs and exit 0 (not enter REPL)."""
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 3)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    called = []
    import agent8088.cli as _cli
    def _fake_cmd_logs(args):
        called.append(args)
        return 0
    monkeypatch.setattr(_cli, "cmd_logs", _fake_cmd_logs)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--logs"])
    rc = _cli.main()
    assert called, "--logs did not dispatch to cmd_logs"
    # main() should not fall through to the REPL.
    assert rc in (0, None)


def test_main_logs_follow_passes_follow_arg(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    _seed_log(log_dir, 1)
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path)
    captured = {}
    import agent8088.cli as _cli
    def _fake_cmd_logs(args):
        captured["logs"] = getattr(args, "logs", None)
        return 0
    monkeypatch.setattr(_cli, "cmd_logs", _fake_cmd_logs)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--logs", "follow"])
    _cli.main()
    assert captured.get("logs") == "follow"