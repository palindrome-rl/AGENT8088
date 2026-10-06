"""schedule_task must honour AGENT8088_HOME isolation.

The live audit showed /schedule add writing to the user's real crontab while
running under a scratch AGENT8088_HOME -- the only path in the whole audit
that leaked outside its sandbox. The configured home is an isolation request:
a scratch home must not register anything with a global scheduler.
"""
import sys

import pytest

import agent8088.engine as E


@pytest.fixture
def scratch_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENT8088_HOME", str(home))
    yield home


def _crontab_calls(monkeypatch, results):
    """Record every crontab subprocess launch; return the call list."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return results.get(tuple(cmd) if isinstance(cmd, list) else cmd)

    monkeypatch.setattr(E.subprocess, "run", fake_run)
    return calls


def test_isolated_home_add_does_not_touch_global_crontab(scratch_home, monkeypatch):
    """With AGENT8088_HOME pointing at a scratch dir, a POSIX add must not
    run the real crontab binary."""
    if sys.platform == "win32":
        pytest.skip("POSIX crontab path")

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    calls = _crontab_calls(monkeypatch, {("crontab", "-l"): R(), ("crontab", "-"): R()})
    result = E._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "daily check"})
    assert "Scheduled" in result
    assert calls == [], "crontab binary must not be invoked under a scratch home"
    # the entry landed in the home-local store instead
    store = scratch_home / "crontab"
    assert store.exists()
    assert "daily check" in store.read_text(encoding="utf-8")


def test_isolated_home_add_does_not_invoke_schtasks(scratch_home, monkeypatch):
    """With AGENT8088_HOME pointing at a scratch dir, a Windows add must not
    register anything with the real Task Scheduler."""
    if sys.platform != "win32":
        pytest.skip("Windows schtasks path")

    calls = []
    real_run = E.subprocess.run

    def fake_run(cmd, **kwargs):
        calls.append(cmd[0] if isinstance(cmd, list) else cmd)
        return real_run(cmd, **kwargs)  # only non-scheduler calls fall through

    monkeypatch.setattr(E.subprocess, "run", fake_run)
    result = E._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "daily check"})
    assert "Scheduled" in result
    assert not any(str(c).endswith("schtasks.exe") or str(c).endswith("schtasks")
                   for c in calls), "schtasks must not be invoked under a scratch home"
    # the registry lives under the scratch home
    assert (scratch_home / "scheduled-tasks.json").exists()


def test_default_home_still_uses_global_crontab(tmp_path, monkeypatch):
    """Without AGENT8088_HOME the historical behaviour is kept: real installs
    schedule for real."""
    if sys.platform == "win32":
        pytest.skip("POSIX crontab path")
    monkeypatch.delenv("AGENT8088_HOME", raising=False)

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    calls = _crontab_calls(monkeypatch, {("crontab", "-l"): R(), ("crontab", "-"): R()})
    result = E._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "daily check"})
    assert "Scheduled" in result
    assert calls, "the real crontab binary is expected on a real install"


def test_isolated_home_list_round_trips(scratch_home, monkeypatch):
    """add then list under a scratch home sees its own entries."""
    if sys.platform == "win32":
        pytest.skip("POSIX crontab path")
    calls = _crontab_calls(monkeypatch, {})
    E._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "round trip"})
    listing = E._exec_cron({"action": "list"})
    assert "round trip" in listing
    assert calls == []


def test_isolated_home_remove_deletes_from_local_store(scratch_home, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("POSIX crontab path")
    calls = _crontab_calls(monkeypatch, {})
    E._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "to be removed"})
    result = E._exec_cron({"action": "remove", "task": "to be removed"})
    assert "Removed" in result
    assert "to be removed" not in (scratch_home / "crontab").read_text(encoding="utf-8")
    assert calls == []