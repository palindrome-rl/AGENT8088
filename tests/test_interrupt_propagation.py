"""ESC/Stop must end the turn from inside tools too, and tool errors must name
what failed.

AgentInterrupted subclasses Exception, so exec_tool's catch-all turned an
interrupt raised inside a tool (a sub-agent, a document run) into an
"Error: " result and the model carried on. A sub-agent also ran with no
interrupt_check at all, and a long shell command ignored Stop until it ended.
"""
import json
import sys
import time
from unittest.mock import patch

import pytest


def _profile():
    return {"name": "web", "description": "d", "tools": ["read_text"], "max_turns": 4,
            "permission": "", "model": "inherit", "builtin": True, "system_prompt": "x"}


def _spawn(engine, child):
    specs = {"web": _profile()}
    with patch.dict(engine.SUBAGENT_SPECS, specs, clear=True), \
         patch.object(engine, "load_subagent_specs", return_value=specs), \
         patch.object(engine, "run_agent", side_effect=child):
        return engine._exec_subagent({"agent_type": "web", "task": "look"})


def test_subagent_inherits_the_parent_interrupt_check(engine, monkeypatch):
    check = lambda: False  # noqa: E731
    monkeypatch.setattr(engine, "_document_interrupt", check)
    seen = {}

    def child(messages, **kwargs):
        seen.update(kwargs)
        return "done"

    _spawn(engine, child)
    assert seen["interrupt_check"] is check


def test_subagent_interrupt_reaches_the_parent(engine):
    def child(messages, **kwargs):
        raise engine.AgentInterrupted()

    with pytest.raises(engine.AgentInterrupted):
        _spawn(engine, child)


def test_subagent_failure_is_a_structured_tool_error(engine):
    def child(messages, **kwargs):
        raise RuntimeError("model went away")

    result = _spawn(engine, child)
    assert "Sub-agent 'web' failed: model went away" in result
    assert '"suggested_action"' in result and '"code": "subagent_failed"' in result


def test_exec_tool_reraises_agent_interrupted(engine, monkeypatch):
    def interrupted(name, args, depth=0):
        raise engine.AgentInterrupted()

    monkeypatch.setattr(engine, "run_tool", interrupted)
    with pytest.raises(engine.AgentInterrupted):
        engine.exec_tool("read_text", json.dumps({"filename": "x"}))


def test_file_not_found_names_the_file(engine, monkeypatch):
    def missing(name, args, depth=0):
        raise FileNotFoundError(2, "No such file or directory", "/work/notes/plan.md")

    monkeypatch.setattr(engine, "run_tool", missing)
    result = engine.exec_tool("read_text", json.dumps({"filename": "x"}))
    assert result.startswith("Error: File not found: /work/notes/plan.md")


def test_missing_program_names_it_and_how_to_install(engine, monkeypatch):
    def missing(name, args, depth=0):
        raise FileNotFoundError(2, "No such file or directory", "rg")

    monkeypatch.setattr(engine, "run_tool", missing)
    result = engine.exec_tool("read_text", json.dumps({"filename": "x"}))
    assert result.startswith("Error: Program not found: rg")
    assert "ripgrep" in result


def test_permission_error_names_the_path(engine, monkeypatch):
    def denied(name, args, depth=0):
        raise PermissionError(13, "Permission denied", "/etc/shadow")

    monkeypatch.setattr(engine, "run_tool", denied)
    assert "/etc/shadow" in engine.exec_tool("read_text", json.dumps({"filename": "x"}))


def test_empty_exception_text_still_says_something(engine, monkeypatch):
    monkeypatch.setattr(engine, "run_tool",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert "RuntimeError" in engine.exec_tool("read_text", json.dumps({"filename": "x"}))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
def test_shell_timeout_keeps_the_last_output(engine):
    out = engine._exec_process(["sh", "-c", "echo waiting-for-lock; sleep 30"], timeout=1)
    assert out.startswith("Command timed out after 1s.")
    assert "waiting-for-lock" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
def test_stop_kills_a_running_command_promptly(engine, monkeypatch, tmp_path):
    started = time.monotonic()
    monkeypatch.setattr(engine, "_document_interrupt",
                        lambda: time.monotonic() - started > 0.5)
    with pytest.raises(engine.AgentInterrupted):
        engine._exec_process(["sh", "-c", "sleep 30"], timeout=30)
    assert time.monotonic() - started < 5, "Stop waited for the command to finish"
    assert not engine._RUNNING_COMMANDS


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
def test_commands_still_finish_normally_under_an_interrupt_check(engine, monkeypatch):
    monkeypatch.setattr(engine, "_document_interrupt", lambda: False)
    assert engine._exec_process(["sh", "-c", "echo hi"], timeout=10) == "hi"


def test_interruptible_sleep_without_a_check_is_plain_sleep(engine, monkeypatch):
    slept = []
    monkeypatch.setattr(engine.time, "sleep", slept.append)
    engine._interruptible_sleep(2.5)
    assert slept == [2.5]
