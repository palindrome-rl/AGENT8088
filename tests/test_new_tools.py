def test_cron_rejects_bad_schedule(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_exec_shell_command",
                        lambda cmd, timeout=25: calls.append(cmd) or "ok")
    out = engine._exec_cron({"action": "add", "schedule": "not a cron", "task": "hi"})
    assert "Invalid" in out
    assert not calls  # crontab never touched


def test_cron_add_builds_entry(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_exec_shell_command",
                        lambda cmd, timeout=25: calls.append(cmd) or "ok")
    engine._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "daily report"})
    assert calls and "0 9 * * *" in calls[-1]
    assert "daily report" in calls[-1]


def test_cron_add_requires_task(engine, monkeypatch):
    monkeypatch.setattr(engine, "_exec_shell_command", lambda cmd, timeout=25: "ok")
    assert "requires a task" in engine._exec_cron({"action": "add", "schedule": "0 9 * * *"})


def test_cron_list_filters_by_marker(engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_exec_shell_command",
                        lambda cmd, timeout=25: seen.setdefault("cmd", cmd) or "")
    engine._exec_cron({"action": "list"})
    assert "agent8088" in seen["cmd"]


def test_cron_unknown_action(engine):
    assert "Unknown" in engine._exec_cron({"action": "explode"})


def test_cron_escapes_quotes_in_task(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_exec_shell_command",
                        lambda cmd, timeout=25: calls.append(cmd) or "ok")
    engine._exec_cron({"action": "add", "schedule": "* * * * *", "task": "it's fine"})
    # A raw single quote would break out of the shell-quoted task string.
    assert "'\\''" in calls[-1]
