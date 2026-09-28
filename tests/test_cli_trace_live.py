"""The trace export survives a run that is stopped mid-turn.

A harness watchdog stops agent8088 with SIGTERM (then SIGKILL). The export was
written only when a turn ended, so a stopped run left no messages and no steps.
"""
import importlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cli(engine):
    from agent8088 import cli as module
    return importlib.reload(module)


def test_each_step_rewrites_the_open_export(cli, tmp_path):
    path = tmp_path / "trace.json"
    cli.S.trace_path = str(path)
    cli.S.messages[:] = [{"role": "user", "content": "task"}]
    trace = cli._start_live_turn("task")
    trace.append({"turn": 1, "type": "tool_calls", "tools": ["execute_shell"]})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["messages"] == [{"role": "user", "content": "task"}]
    [turn] = data["trace"]
    assert turn["in_progress"] and turn["steps"][0]["type"] == "tool_calls"

    cli._record_trace("task", trace, 1.0)
    assert cli.S.live_turn is None
    [turn] = cli.S.conversation_trace
    assert "in_progress" not in turn and "usage" in turn


def test_turn_usage_reports_the_outermost_turn(engine):
    assert engine.turn_usage() is None
    budget = engine._TurnBudget()
    budget.add_tokens(120, 30)
    engine._outer_turn_budget = budget
    assert engine.turn_usage() == {"input_tokens": 120, "output_tokens": 30}


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigterm_saves_the_turn_so_far_and_exits(tmp_path):
    path = tmp_path / "trace.json"
    ready = tmp_path / "ready"
    script = f"""
import os, sys, time
os.environ["AGENT8088_CONFIG"] = {str(ROOT / "_no_such_config.txt")!r}
os.environ["AGENT8088_SANDBOX"] = "local"
sys.path.insert(0, {str(ROOT / "src")!r})
from agent8088 import cli
cli.S.trace_path = {str(path)!r}
cli.S.messages[:] = [{{"role": "user", "content": "task"}}]
cli._save_trace_on_sigterm()
trace = cli._start_live_turn("task")
trace.append({{"turn": 1, "type": "tool_calls", "tools": ["execute_shell"]}})
cli.S.messages.append({{"role": "assistant", "content": "working"}})
open({str(ready)!r}, "w").close()
time.sleep(60)   # a long model call
"""
    env = {**os.environ, "ANONYMIZED_TELEMETRY": "false", "POSTHOG_DISABLED": "1"}
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while not ready.exists():
        assert proc.poll() is None, proc.communicate()
        assert time.time() < deadline, "child never got ready"
        time.sleep(0.1)
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 128 + signal.SIGTERM, err
    assert "stopped by SIGTERM" in out and "run ended: watchdog" in out
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["messages"][-1] == {"role": "assistant", "content": "working"}
    [turn] = data["trace"]
    assert turn["interrupted"] and turn["stop_reason"] == "SIGTERM"
    assert turn["steps"][0]["type"] == "tool_calls"


def test_run_end_reason_names_how_the_turn_ended(cli):
    cli.S.conversation_trace[:] = [{"interrupted": False}]
    reason = cli._run_end_reason
    assert reason([{"type": "tool_calls"}, {"type": "final_answer"}]) == "answered"
    assert reason([{"type": "budget_exceeded",
                    "content": "Turn budget exceeded: 866 seconds elapsed (limit 865)."}]) == "time_budget"
    assert reason([{"type": "budget_wrap_up"}]) == "turn_limit"
    assert reason([{"type": "max_turns"}]) == "turn_limit"
    assert reason([{"type": "model_error", "detail": "Connection refused"}]) == "model_error"
    assert reason([]) == "unknown" and reason(None) == "unknown"
    cli.S.conversation_trace[:] = [{"interrupted": True}]
    assert reason([{"type": "final_answer"}]) == "interrupted"


def test_model_error_step_records_the_failure(engine):
    step = engine._model_error_step(3, ConnectionError("vLLM down"))
    assert step == {"turn": 3, "type": "model_error",
                    "error_type": "ConnectionError", "detail": "vLLM down"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_kill_running_commands_stops_a_command_in_flight(engine):
    import threading
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("out", engine._exec_process("sleep 30", timeout=60, shell=True)))
    started = time.time()
    worker.start()
    while not engine._RUNNING_COMMANDS and time.time() - started < 10:
        time.sleep(0.05)
    assert engine.kill_running_commands() == 1
    worker.join(timeout=10)
    assert not worker.is_alive() and time.time() - started < 10
    assert not engine._RUNNING_COMMANDS
    assert engine.kill_running_commands() == 0
