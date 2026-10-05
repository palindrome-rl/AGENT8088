"""memory_status(): a one-line health report that a degraded memory can't hide behind.

Every memory entry point still swallows its failures (memory must never break
a turn), but they used to go to log.debug only, so a locked database looked
exactly like "nothing to remember".
"""
import logging

import pytest

from agent8088 import memory


@pytest.fixture(autouse=True)
def _clean():
    memory.reset()
    yield
    memory.reset()


def test_off_is_ok():
    memory.configure(config={"memory": "0"})
    assert memory.memory_status() == {"engine": "off", "ok": True, "error": "", "fix": ""}


def test_healthy_native_store(tmp_path):
    memory.configure(config={"memory": "1"}, db_path=str(tmp_path / "m.db"))
    status = memory.memory_status()
    assert status["engine"] == "native" and status["ok"] and not status["error"]


def test_unopenable_store_is_reported_with_a_fix(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    memory.configure(config={"memory": "1"}, db_path=str(blocker / "sub" / "m.db"))
    with caplog.at_level(logging.WARNING, logger="agent8088.memory"):
        status = memory.memory_status()
    assert status["ok"] is False
    assert status["error"]
    assert "memory_db_path" in status["fix"]
    assert any("memory open failed" in r.getMessage() for r in caplog.records)


def test_recall_failure_is_recorded_and_cleared_by_a_later_success(tmp_path, monkeypatch, caplog):
    memory.configure(config={"memory": "1"}, db_path=str(tmp_path / "m.db"))
    active = memory.store()
    original = active.search

    def broken(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(active, "search", broken)
    with caplog.at_level(logging.WARNING, logger="agent8088.memory"):
        assert memory.recall("what do I like") == []
        assert memory.recall("what do I like") == []
    assert [r.getMessage() for r in caplog.records].count(
        "memory recall failed: database is locked") == 1  # once per distinct error
    status = memory.memory_status()
    assert status["ok"] is False and status["error"] == "recall: database is locked"

    monkeypatch.setattr(active, "search", original)
    memory.recall("what do I like")
    assert memory.memory_status()["ok"] is True


def test_mem0_fallback_is_ok_but_explained(tmp_path, monkeypatch):
    memory.configure(config={"memory": "1", "memory_engine": "mem0"},
                     db_path=str(tmp_path / "m.db"))

    class _Unavailable:
        last_error = "mem0ai is not installed"

        def __init__(self, *a, **k):
            pass

        def available(self):
            return False

    import agent8088.memory.mem0_store as mem0_store
    monkeypatch.setattr(mem0_store, "Mem0MemoryStore", _Unavailable)
    status = memory.memory_status()
    assert status["engine"] == "native" and status["ok"] is True
    assert "mem0ai is not installed" in status["error"]
    assert "pip install mem0ai" in status["fix"]
