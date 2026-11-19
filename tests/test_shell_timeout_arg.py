"""execute_shell accepts an optional per-call `timeout`, clamped to the
configured ceiling, so a long build does not need every command's default
raised."""
import os

import pytest


def _host_shell(engine, declared=5):
    spec = engine.TOOL_SPECS["execute_shell"]
    spec["host"] = "1"          # run via bash on the host; no sandbox in tests
    spec["timeout"] = declared
    return spec


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX sleep")
def test_timeout_arg_extends_a_short_default(engine):
    _host_shell(engine, declared=1)
    out = engine.run_tool("execute_shell", {"command": "sleep 2; echo ok", "timeout": 5})
    assert "ok" in out
    assert "timed out" not in out


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX sleep")
def test_timeout_arg_can_shorten(engine):
    _host_shell(engine, declared=30)
    out = engine.run_tool("execute_shell", {"command": "sleep 5; echo late", "timeout": 1})
    assert "timed out after 1s" in out


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX sleep")
def test_timeout_arg_is_clamped_to_the_ceiling(engine, monkeypatch):
    _host_shell(engine, declared=1)
    monkeypatch.setattr(engine, "MAX_TOOL_TIMEOUT_SECONDS", 2)
    out = engine.run_tool("execute_shell", {"command": "sleep 4; echo late", "timeout": 999})
    assert "timed out after 2s" in out


def test_non_numeric_timeout_falls_back_to_declared(engine):
    _host_shell(engine, declared=5)
    out = engine.run_tool("execute_shell", {"command": "echo fine", "timeout": "soon"})
    assert "fine" in out


def test_wait_commands_are_recognised(engine):
    wait = engine._is_wait_command
    assert wait("execute_shell", {"command": "sleep 30; tail -5 /tmp/build.log"})
    assert wait("execute_shell", {"command": "cd /app && sleep 5 && curl -s localhost:5000"})
    assert not wait("execute_shell", {"command": "ls -la"})
    assert not wait("execute_shell", {"command": "grep sleep notes.txt"})
    assert not wait("read_text", {"command": "sleep 5"})
