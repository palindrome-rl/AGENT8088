"""Real crontab commands must execute unattended and preserve task text."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent8088 import engine as E

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX crontab commands")


def test_scheduled_command_reaches_the_agent_with_unattended_mode(monkeypatch, tmp_path):
    calls = []

    def capture(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    agent = tmp_path / "agent with spaces"
    agent.write_text('#!/bin/sh\nprintf "%s\\n" "$AGENT8088_UNATTENDED"\ncat\n')
    agent.chmod(0o700)
    monkeypatch.setattr(E, "_schedule_isolation_active", lambda: False)
    monkeypatch.setattr(E, "SHELL_CWD", tmp_path)
    monkeypatch.setattr(E.shutil, "which", lambda name: str(agent))
    with monkeypatch.context() as patch:
        patch.setattr(E.subprocess, "run", capture)
        result = E._exec_cron({"action": "add", "schedule": "0 9 * * *",
                              "task": "report 100% of 'results'"})
    assert result.startswith("Scheduled:")
    command = calls[-1][1]["input"].split(" ", 5)[5].strip()
    # crontab treats unescaped % as a newline, even inside shell quotes.
    assert all(command[i - 1] == "\\" for i, ch in enumerate(command) if ch == "%")
    command = command.replace(r"\%", "%")  # cron removes the escape before sh
    env = {"PATH": "/usr/bin:/bin"}
    ran = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True,
                         env=env, check=True)
    assert ran.stdout == "1\nreport 100% of 'results'\n"


def test_crontab_read_error_never_overwrites_the_existing_schedule(monkeypatch):
    calls = []

    def denied(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="permission denied")

    monkeypatch.setattr(E, "_schedule_isolation_active", lambda: False)
    monkeypatch.setattr(E.subprocess, "run", denied)
    result = E._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "report"})
    assert "permission denied" in result
    assert calls == [["crontab", "-l"]]


@pytest.mark.parametrize("isolated", [False, True])
def test_removing_a_task_preserves_other_tasks_and_round_trips_special_text(monkeypatch, tmp_path, isolated):
    stored = [""]

    def crontab(command, **kwargs):
        if command == ["crontab", "-"]:
            stored[0] = kwargs["input"]
        return SimpleNamespace(returncode=0, stdout=stored[0], stderr="")

    monkeypatch.setattr(E, "_schedule_isolation_active", lambda: isolated)
    monkeypatch.setattr(E, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(E.subprocess, "run", crontab)
    tasks = ["report", "report weekly", "100% of 'a | b' # agent8088",
             r"printf '%s\n' report"]
    for task in tasks:
        assert E.schedule_task("add", "0 9 * * *", task)["ok"]
    assert [entry["task"] for entry in E.schedule_task("list")["entries"]] == tasks
    assert E.schedule_task("remove", task="report")["ok"]
    assert [entry["task"] for entry in E.schedule_task("list")["entries"]] == tasks[1:]
