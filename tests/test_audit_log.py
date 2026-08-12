"""Append-only audit trail.

_log goes to a logger with no configured sink, so there was no durable record of
which tool ran or which permission decision was made. The audit log is that
record — and because it is a record and not a gate, a broken sink must never
break an agent turn.
"""
import json
import os

import pytest

from agent8088 import engine as A


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(A, "AUDIT_LOG_PATH", path)
    monkeypatch.setattr(A, "AUDIT_ENABLED", True)
    return path


def test_writes_one_json_line_per_event(audit_path):
    A._audit("tool_call", tool="write_file", decision="allowed", detail="/tmp/x")
    A._audit("tool_call", tool="execute_shell", decision="blocked", detail="rm -rf /")
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "tool_call"
    assert first["tool"] == "write_file"
    assert first["decision"] == "allowed"
    assert "ts" in first and "permission_mode" in first


def test_disabled_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(A, "AUDIT_LOG_PATH", path)
    monkeypatch.setattr(A, "AUDIT_ENABLED", False)
    A._audit("tool_call", tool="write_file", decision="allowed")
    assert not path.exists()


def test_secrets_are_redacted_in_detail(audit_path, monkeypatch):
    monkeypatch.setattr(A, "_SECRET_VALUES", ["sk-live-abcdef0123456789"])
    A._audit("tool_call", tool="http_post",
             decision="blocked", detail="body=sk-live-abcdef0123456789")
    written = audit_path.read_text()
    assert "sk-live-abcdef0123456789" not in written
    assert "[redacted]" in written


def test_detail_is_truncated(audit_path):
    A._audit("tool_call", tool="execute_shell", decision="allowed", detail="x" * 5000)
    entry = json.loads(audit_path.read_text().strip().splitlines()[0])
    assert len(entry["detail"]) <= A.AUDIT_MAX_DETAIL


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_log_file_is_private(audit_path):
    A._audit("tool_call", tool="read_file", decision="allowed")
    assert oct(audit_path.stat().st_mode)[-3:] == "600"


def test_write_failure_never_raises(monkeypatch, tmp_path):
    """An unwritable audit path must not take down the agent turn."""
    monkeypatch.setattr(A, "AUDIT_ENABLED", True)
    monkeypatch.setattr(A, "AUDIT_LOG_PATH", tmp_path / "deep" / "a.jsonl")

    def _boom(*a, **kw):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(A.Path, "mkdir", _boom)
    A._audit("tool_call", tool="read_file", decision="allowed")  # must not raise


# --- Integration: real decisions must land in the log ---

@pytest.fixture
def audited_engine(engine, monkeypatch, tmp_path):
    """Engine with the audit log on and tmp_path as the only writable root."""
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(engine, "AUDIT_LOG_PATH", path)
    monkeypatch.setattr(engine, "AUDIT_ENABLED", True)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    engine._audit_path = path
    return engine


def _entries(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_blocked_write_is_audited(audited_engine, monkeypatch, tmp_path):
    monkeypatch.setattr(audited_engine, "PERMISSION_MODE", "readonly")
    result = audited_engine.run_tool(
        "write_file", {"filename": str(tmp_path / "x.txt"), "content": "hi"})
    assert result.startswith("ESCALATION_REQUEST\x1f")
    assert any(e["decision"] == "blocked" and e["tool"] == "write_file"
               for e in _entries(audited_engine._audit_path))


def test_allowed_write_is_audited(audited_engine, monkeypatch, tmp_path):
    monkeypatch.setattr(audited_engine, "PERMISSION_MODE", "edit")
    target = tmp_path / "x.txt"
    result = audited_engine.run_tool(
        "write_file", {"filename": str(target), "content": "hi"})
    assert "Wrote" in result
    entries = _entries(audited_engine._audit_path)
    assert any(e["decision"] == "allowed" and e["tool"] == "write_file"
               and str(target) in e["detail"] for e in entries)


def test_hardline_refusal_is_audited(audited_engine, tmp_path):
    """A sensitive-file read is denied outright — that must be recorded too."""
    result = audited_engine.run_tool("read_text",
                                     {"filename": str(tmp_path / ".env")})
    assert "sensitive file denied" in result
    entries = _entries(audited_engine._audit_path)
    assert any(e["decision"] == "denied" and e["reason"] == "sensitive_path"
               for e in entries)


def test_ordinary_read_is_not_audited(audited_engine, tmp_path):
    """Reads are not a gated mode — the log records decisions, not every call."""
    target = tmp_path / "notes.txt"
    target.write_text("hello")
    audited_engine.run_tool("read_text", {"filename": str(target)})
    assert not audited_engine._audit_path.exists()


def test_audit_is_off_by_default(engine):
    """Single-user CLI use should not start writing a log file unasked."""
    assert engine.AUDIT_ENABLED is False
