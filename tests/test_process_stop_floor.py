"""The always-on shell floor refuses stopping processes by name, in every mode."""
import os
import sys

import pytest

BY_NAME = [
    "pkill python",
    "pkill -f server.py",
    "sudo pkill -9 node",
    "killall python3",
    "killall -9 node",
    "taskkill /IM python.exe",
    "taskkill /F /IM node.exe /T",
    'taskkill /F /FI "IMAGENAME eq python.exe"',
    "Stop-Process -Name node",
    "Stop-Process -Name node -Force",
    "Get-Process python | Stop-Process",
    "Get-Process python | Stop-Process -Force",
    "ps aux | grep node | awk '{print $2}' | xargs kill",
    "pgrep -f server | xargs kill -9",
    "kill $(pgrep node)",
    "kill -9 `pidof python`",
    "kill $(cat server.pid)",
    "kill -9 -1",
    "kill 0",
    "wmic process where \"name='python.exe'\" delete",
    "Get-Process node | ForEach-Object { $_.Kill() }",
    "(Get-Process node).Kill()",
    "python -c \"import psutil; [p.kill() for p in psutil.process_iter()]\"",
    "powershell -Command \"Stop-Process -Name node\"",
    "bash -c 'pkill python'",
    "cmd /c taskkill /IM python.exe",
    "cd app && npm test; pkill node",
    "FOO=1 pkill python",
]

BY_PID = [
    "kill 12345",
    "kill -9 12345",
    "kill -TERM 12345 12346",
    "kill -s KILL 12345",
    "kill -l",
    "kill %1",
    "kill $!",
    "taskkill /PID 12345 /F",
    "taskkill /F /T /PID 12345",
    "Stop-Process -Id 12345",
    "Stop-Process -Id 12345 -Force",
]

UNRELATED = [
    "ls -la",
    "echo pkill python",
    'echo "taskkill /IM python.exe"',
    "grep -r pkill src/",
    "man pkill",
    "pgrep -f server",
    "ps aux",
    "git commit -m 'stop using pkill'",
    "Get-Process python",
    "tasklist /FI \"IMAGENAME eq python.exe\"",
    "docker kill my-container",
    "npm run kill-port",
    "python -m pytest tests",
]


@pytest.mark.parametrize("command", BY_NAME)
def test_stopping_processes_by_name_is_refused(engine, command):
    assert engine._hard_blocked_shell(command), command
    assert engine.last_refusal_was_process_stop(), command


@pytest.mark.parametrize("command", BY_PID)
def test_stopping_a_process_by_its_pid_is_allowed(engine, command):
    assert not engine._hard_blocked_shell(command), command


@pytest.mark.parametrize("command", UNRELATED)
def test_commands_that_only_mention_these_tools_are_untouched(engine, command):
    assert not engine._hard_blocked_shell(command), command


def test_agent8088_and_its_parent_cannot_be_stopped(engine):
    for pid in (os.getpid(), os.getppid()):
        for command in (f"kill {pid}", f"kill -9 {pid}", f"taskkill /PID {pid} /F",
                        f"Stop-Process -Id {pid}"):
            assert engine._hard_blocked_shell(command), command
            assert engine.last_refusal_was_process_stop(), command


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("command", ["pkill python", "bash -c 'pkill python'", "taskkill /IM python.exe",
                                     "Get-Process python | Stop-Process"])
def test_refusal_holds_under_both_lexers(engine, monkeypatch, platform, command):
    monkeypatch.setattr(sys, "platform", platform)
    assert engine._hard_blocked_shell(command), command


@pytest.mark.parametrize("mode", ["readonly", "plan-only", "edit", "full-auto"])
def test_refused_in_every_permission_mode(engine, monkeypatch, mode):
    monkeypatch.setattr(engine, "PERMISSION_MODE", mode)
    assert engine.check_permission("shell", "pkill python") is False
    assert engine.check_permission("shell", "taskkill /IM node.exe") is False


def test_a_command_that_is_blocked_for_another_reason_is_not_reported_as_a_process_stop(engine):
    assert engine._hard_blocked_shell("rm -rf /")
    assert not engine.last_refusal_was_process_stop()
    assert not engine._hard_blocked_shell("ls")
    assert not engine.last_refusal_was_process_stop()


def test_the_refusal_names_the_alternative(engine):
    message = engine.PROCESS_STOP_REFUSAL
    assert "by name" in message and "PID" in message


def test_the_tool_call_returns_the_specific_refusal(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    result = engine._run_tool("execute_shell", {"command": "pkill python"})
    assert result == engine.PROCESS_STOP_REFUSAL


def test_other_hard_blocks_keep_the_generic_message(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    result = engine._run_tool("execute_shell", {"command": "rm -rf /"})
    assert "forbidden by Agent8088's safety policy" in result
