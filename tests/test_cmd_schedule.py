from agent8088 import cli
from agent8088 import engine as A


def _run(rest, monkeypatch, fake_schedule_task):
    monkeypatch.setattr(A, "schedule_task", fake_schedule_task)
    with cli.console.capture() as capture:
        cli.cmd_schedule(rest)
    return capture.get()


def test_list_shows_entries(monkeypatch):
    calls = []

    def fake(action="list", schedule="", task=""):
        calls.append((action, schedule, task))
        return {"ok": True, "detail": "", "entries": [
            {"schedule": "0 9 * * *", "task": "check disk space"},
        ]}

    out = _run("list", monkeypatch, fake)
    assert calls == [("list", "", "")]
    assert "0 9 * * *" in out
    assert "check disk space" in out


def test_list_with_no_entries_shows_detail(monkeypatch):
    out = _run("list", monkeypatch, lambda **_: {"ok": True, "detail": "No scheduled tasks.", "entries": []})
    assert "No scheduled tasks." in out
    assert "usage" not in out


def test_bare_schedule_shows_usage_only(monkeypatch):
    out = _run("", monkeypatch, lambda **_: {"ok": True, "detail": "", "entries": [
        {"schedule": "0 9 * * *", "task": "check disk space"},
    ]})
    assert "usage" in out
    assert "/schedule add" in out
    assert "check disk space" not in out


def test_add_with_quoted_cron(monkeypatch):
    calls = []

    def fake(action="list", schedule="", task=""):
        calls.append((action, schedule, task))
        return {"ok": True, "detail": f"Scheduled: {schedule}", "entries": []}

    out = _run('add "0 9 * * *" check disk space and report', monkeypatch, fake)
    assert calls == [("add", "0 9 * * *", "check disk space and report")]
    assert "Scheduled" in out


def test_add_with_bare_cron_fields(monkeypatch):
    calls = []

    def fake(action="list", schedule="", task=""):
        calls.append((action, schedule, task))
        return {"ok": True, "detail": "", "entries": []}

    _run("add 0 9 * * * check disk space", monkeypatch, fake)
    assert calls == [("add", "0 9 * * *", "check disk space")]


def test_add_missing_task_shows_usage(monkeypatch):
    out = _run('add "0 9 * * *"', monkeypatch, lambda **_: {"ok": True, "detail": "", "entries": []})
    assert "Missing task text" in out


def test_remove_by_index_resolves_task_text(monkeypatch):
    calls = []

    def fake(action="list", schedule="", task=""):
        calls.append((action, schedule, task))
        if action == "list":
            return {"ok": True, "detail": "", "entries": [
                {"schedule": "0 9 * * *", "task": "check disk space"},
            ]}
        return {"ok": True, "detail": "Removed.", "entries": []}

    out = _run("remove 1", monkeypatch, fake)
    assert ("remove", "", "check disk space") in calls
    assert "Removed" in out


def test_remove_by_out_of_range_index_errors(monkeypatch):
    out = _run("remove 5", monkeypatch, lambda **_: {"ok": True, "detail": "", "entries": []})
    assert "No scheduled task #5" in out


def test_remove_by_task_text(monkeypatch):
    calls = []

    def fake(action="list", schedule="", task=""):
        calls.append((action, schedule, task))
        return {"ok": True, "detail": "Removed.", "entries": []}

    _run("remove check disk space", monkeypatch, fake)
    assert calls == [("remove", "", "check disk space")]


def test_unknown_subcommand_shows_usage(monkeypatch):
    out = _run("bogus", monkeypatch, lambda **_: {"ok": True, "detail": "", "entries": []})
    assert "usage" in out
