"""Privacy and session identity regressions found during PR review."""
import asyncio
import json

import pytest

from agent8088 import cli, engine, web_server as W


def test_web_source_does_not_change_later_cli_sessions(monkeypatch):
    monkeypatch.setattr(cli, "S", cli.Session())
    monkeypatch.setattr(W, "_cli", None)
    W._cl()
    assert cli.build_session_data("web-chat")["source"] == "web"
    monkeypatch.setattr(cli, "S", cli.Session())
    assert cli.build_session_data("cli-chat")["source"] == "cli"


def test_web_session_writer_keeps_private_file_protection(tmp_path, monkeypatch):
    protected = []
    monkeypatch.setattr(engine, "_protect_private_file", lambda path: protected.append(path))
    path = tmp_path / "session.json"
    W._save_session_file(path, {"messages": ["private conversation"]})
    assert len(protected) == 1
    assert json.loads(path.read_text())["messages"] == ["private conversation"]


def test_failed_private_write_preserves_previous_web_session(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    path.write_text('{"messages": ["last good save"]}')
    def fail_protection(path):
        raise OSError("cannot protect private file")
    monkeypatch.setattr(engine, "_protect_private_file", fail_protection)
    with pytest.raises(OSError, match="cannot protect"):
        W._save_session_file(path, {"messages": ["new save"]})
    assert json.loads(path.read_text())["messages"] == ["last good save"]
    assert list(tmp_path.iterdir()) == [path]


def test_web_plan_completion_keeps_owning_session_id(monkeypatch):
    async def fake_chat(ws, frame, A, C):
        frame["_session_id"] = "a" * 32
        assert frame["session_id"] == "a" * 32
    monkeypatch.setattr(W, "_handle_chat", fake_chat)
    monkeypatch.setattr(engine, "enter_plan_mode", lambda: None)
    frame = {"type": "command", "command": "plan", "args": "write a report",
             "session_id": "a" * 32}
    asyncio.run(W._handle_command(None, frame, engine, cli))
    assert frame.get("_session_id") == "a" * 32
