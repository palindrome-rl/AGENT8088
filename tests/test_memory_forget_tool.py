"""The model-facing memory_forget tool, dispatched through run_tool.

memory.forget() itself is covered in test_memory_forget.py; this file only
proves run_tool("memory_forget", ...) actually reaches it -- the dispatch
wiring, not the deletion logic.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_memory(engine):
    yield
    engine.memory.reset()


def _configure(engine, tmp_path, **overrides):
    config = {"memory": "1", "memory_engine": "native", "memory_capture": "1"}
    config.update(overrides)
    engine.memory.configure(config=config, db_path=str(tmp_path / "memory.sqlite3"))


def test_memory_forget_tool_reaches_the_real_forget(engine, tmp_path):
    _configure(engine, tmp_path)
    engine.memory.store().add("the user's facebook username is tayyab2132",
                              user_id="owner", source="user")

    result = engine.run_tool("memory_forget", {"query": "facebook username"})

    assert "tayyab2132" in result
    assert engine.memory.recall("facebook username", identity="owner") == []


def test_memory_forget_tool_is_registered():
    from agent8088.engine import TOOL_SPECS
    assert "memory_forget" in TOOL_SPECS
    assert TOOL_SPECS["memory_forget"]["mode"] == "memory"
