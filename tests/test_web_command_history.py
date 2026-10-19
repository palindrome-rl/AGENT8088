"""Typed /commands survive a reload of the web chat (issue sheet row 23)."""
from types import SimpleNamespace as NS

import pytest

from agent8088 import web_server


@pytest.fixture
def chat(monkeypatch):
    session = NS(id="s1", messages=[{"role": "user", "content": "hi"},
                                    {"role": "assistant", "content": "hello"}])
    monkeypatch.setattr(web_server, "_cl", lambda: NS(S=session))
    monkeypatch.setattr(web_server, "_COMMAND_LOG", {})
    return session


def _ran(command, args, result, session_id="s1"):
    web_server._log_command({"command": command, "args": args, "_session_id": session_id},
                            {"type": "command_result", "command": command, "result": result})


def test_a_command_is_shown_where_it_ran(chat):
    _ran("model", "", "model: qwen")
    chat.messages.append({"role": "user", "content": "next"})
    shown = web_server._with_command_log("s1", list(chat.messages))
    assert [m["content"] for m in shown] == ["hi", "hello", "/model", "model: qwen", "next"]
    assert shown[3]["format"] == "terminal"


def test_args_are_kept_and_empty_output_shows_only_the_command(chat):
    _ran("maxturns", "120", "")
    shown = web_server._with_command_log("s1", list(chat.messages))
    assert shown[-1] == {"role": "user", "content": "/maxturns 120"}


def test_session_operations_and_other_chats_are_not_mixed_in(chat):
    _ran("reset", "", "cleared")
    _ran("model", "", "model: other", session_id="s2")
    assert web_server._with_command_log("s1", list(chat.messages)) == chat.messages


def test_a_command_from_before_a_reset_is_dropped(chat):
    _ran("model", "", "model: qwen")
    assert web_server._with_command_log("s1", []) == []
