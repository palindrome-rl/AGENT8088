"""A model waiting for a slow job (a download growing, a server starting) re-sends the same shell command while it waits. The loop
breaker used to answer every identical repeat from the first result ("already ran with this output"), so the model never saw the
file grow, kept asking, and the run was stopped for "repeated tool calls made no progress" after 22 minutes with no app written.

A successful shell command repeated after REPEAT_REFRESH_SECONDS runs again; a failed one, a quick spin, or a run that turned the
refresh off is still answered from the earlier result. Waits written in python, PowerShell, cmd and node count as waits too."""
import json
import time as real_time
from types import SimpleNamespace as NS

CALL = "✿FUNCTION✿: execute_shell ✿ARGS✿: " + json.dumps({"command": "python -c \"import os; print(os.path.getsize('x.bin'))\""})


def _clock(engine, monkeypatch, step):
    now = [1000.0]

    def monotonic():
        now[0] += step
        return now[0]

    fake = NS(**{k: getattr(real_time, k) for k in dir(real_time) if not k.startswith("__")})
    fake.monotonic = monotonic
    monkeypatch.setattr(engine, "time", fake)


def _run(engine, monkeypatch, outputs, calls=3):
    responses = iter([CALL] * calls + ["done"])
    executed = []

    def completion(*a, **kw):
        return NS(choices=[NS(message=NS(content=next(responses)), finish_reason="stop")])

    def exec_tool(*a, **kw):
        executed.append(a)
        return outputs[min(len(executed) - 1, len(outputs) - 1)]

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "exec_tool", exec_tool)
    messages = [{"role": "user", "content": "Wait for the download"}]
    answer = engine.run_agent(messages, max_turns=8, system_prompt="", tools_def=[], allowed_tools={"execute_shell"}, memory_capture=False)
    return answer, executed, messages


def test_identical_shell_call_after_time_has_passed_runs_again(engine, monkeypatch):
    monkeypatch.setattr(engine, "REPEAT_REFRESH_SECONDS", 20)
    _clock(engine, monkeypatch, step=30)            # every clock read is 30 s later than the last
    answer, executed, _ = _run(engine, monkeypatch, ["1000", "2000", "3000"])
    assert answer == "done"
    assert len(executed) == 3                        # the model saw the file grow


def test_quick_identical_repeat_is_still_answered_from_the_first_result(engine, monkeypatch):
    monkeypatch.setattr(engine, "REPEAT_REFRESH_SECONDS", 20)
    _clock(engine, monkeypatch, step=0.001)
    answer, executed, messages = _run(engine, monkeypatch, ["1000"], calls=2)
    assert len(executed) == 1
    assert any("already ran with this output" in m["content"] for m in messages)


def test_a_failed_command_is_not_refreshed(engine, monkeypatch):
    monkeypatch.setattr(engine, "REPEAT_REFRESH_SECONDS", 20)
    _clock(engine, monkeypatch, step=30)
    answer, executed, messages = _run(engine, monkeypatch, ["Command exited with status 1\nboom"], calls=2)
    assert len(executed) == 1
    assert any("already ran with this output" in m["content"] for m in messages)


def test_refresh_can_be_turned_off(engine, monkeypatch):
    monkeypatch.setattr(engine, "REPEAT_REFRESH_SECONDS", 0)
    _clock(engine, monkeypatch, step=30)
    answer, executed, _ = _run(engine, monkeypatch, ["1000"], calls=2)
    assert len(executed) == 1


def test_refresh_is_bounded_and_only_for_shell(engine):
    due = engine._poll_refresh_due
    long_ago = real_time.monotonic() - 3600
    assert due("execute_shell", "ok", long_ago, 0)
    assert not due("execute_shell", "ok", long_ago, engine.REPEAT_REFRESH_MAX)
    assert not due("read_text", "ok", long_ago, 0)
    assert not due("execute_shell", "ok", real_time.monotonic(), 0)
    assert not due("execute_shell", "Error: nope", long_ago, 0)


def test_waits_written_in_other_languages_are_recognised(engine):
    wait = engine._is_wait_command
    for command in ('python -c "import time; time.sleep(25); print(1)"', "powershell -c Start-Sleep 20; dir",
                    "timeout /t 20 && dir", "ping -n 20 127.0.0.1 >nul", 'node -e "setTimeout(()=>{},9000)"'):
        assert wait("execute_shell", {"command": command}), command
    assert not wait("execute_shell", {"command": "python -c \"print('sleepy')\""})
    assert not wait("execute_shell", {"command": "grep -n timeout config.txt"})
