"""Windows-side coverage for the full-uninstall-cleanup fixes:
`_run_windows_uninstall` only ever removed 3 of the 7 PATH entries an install
can add (missing the bundled Git's 3 entries and the bundled Node entry), and
never removed the Windows Task Scheduler entries a `cron_mode` schedule
registers. Follows the fake-`winreg`-module convention already used in
test_windows_uninstall.py.
"""
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

from agent8088 import cli


def test_windows_owned_path_entries_includes_bundled_git_and_node():
    home = Path(r"C:\Users\Example\AppData\Local\agent8088")

    entries = cli._windows_owned_path_entries(home)

    assert home / "git" / "cmd" in entries
    assert home / "git" / "bin" in entries
    assert home / "git" / "usr" / "bin" in entries
    assert home / "node" in entries
    # And the entries _run_windows_uninstall already removed before this fix.
    assert cli._agent8088_link_dir() in entries
    assert home / "bin" in entries
    assert home / "agent8088" / "venv" / "Scripts" in entries


def test_remove_windows_scheduled_tasks_deletes_each_registered_task(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    registry = home / "scheduled-tasks.json"
    registry.write_text(json.dumps([
        {"id": "abc1234567890def", "schedule": "0 9 * * *", "task": "check inbox"},
        {"id": "1112223334445556", "schedule": "*/5 * * * *", "task": "poll"},
    ]), encoding="utf-8")

    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "schtasks.exe" if "schtasks" in name else None)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    removed = cli._remove_windows_scheduled_tasks(home)

    assert removed == 2
    deleted_names = {c[c.index("/TN") + 1] for c in calls if "/TN" in c}
    assert deleted_names == {"Agent8088-abc1234567890def", "Agent8088-1112223334445556"}
    assert all(c[0] == "schtasks.exe" and "/F" in c for c in calls)


def test_remove_windows_scheduled_tasks_noop_without_registry(tmp_path):
    home = tmp_path / "agent8088"
    home.mkdir()

    removed = cli._remove_windows_scheduled_tasks(home)

    assert removed == 0


def test_remove_windows_scheduled_tasks_ignores_malformed_ids(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    (home / "scheduled-tasks.json").write_text(
        json.dumps([{"id": "not-a-valid-hex-id"}, {"id": "abc1234567890def"}]),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda _n: "schtasks.exe")
    monkeypatch.setattr(cli.subprocess, "run",
                         lambda cmd, **kw: calls.append(cmd) or mock.Mock(returncode=0))

    removed = cli._remove_windows_scheduled_tasks(home)

    assert removed == 1
    assert len(calls) == 1


def test_run_windows_uninstall_removes_git_and_node_path_entries(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    values = {
        "Path": (";".join([
            str(home / "git" / "cmd"),
            str(home / "git" / "bin"),
            str(home / "git" / "usr" / "bin"),
            str(home / "node"),
            r"C:\Windows\System32",
        ]), 2),
    }
    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_QUERY_VALUE=1,
        KEY_SET_VALUE=2,
        REG_EXPAND_SZ=2,
        OpenKey=lambda *_args: object(),
        QueryValueEx=lambda _key, name: values[name],
        SetValueEx=lambda _key, name, _reserved, kind, value: values.__setitem__(name, (value, kind)),
        DeleteValue=lambda _key, name: values.pop(name, None),
        CloseKey=lambda _key: None,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(cli, "_windows_processes_in_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_purge_install_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_remove_windows_launcher_dir", lambda _link_dir: None)
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)
    monkeypatch.setattr(cli, "_remove_agent8088_searxng_container", lambda: False)

    assert cli._run_windows_uninstall(home)

    assert values["Path"] == (r"C:\Windows\System32", 2)


def test_run_windows_uninstall_deletes_registered_scheduled_tasks(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent8088"
    home.mkdir()
    (home / "scheduled-tasks.json").write_text(
        json.dumps([{"id": "abc1234567890def"}]), encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: True)
    monkeypatch.setattr(cli, "_windows_processes_in_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_purge_install_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_remove_windows_launcher_dir", lambda _link_dir: None)
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)
    monkeypatch.setattr(cli, "_remove_agent8088_searxng_container", lambda: False)
    monkeypatch.setattr(cli.shutil, "which", lambda _n: "schtasks.exe")
    schtasks_calls = []
    monkeypatch.setattr(cli.subprocess, "run",
                         lambda cmd, **kw: schtasks_calls.append(cmd) or mock.Mock(returncode=0))

    assert cli._run_windows_uninstall(home)

    assert len(schtasks_calls) == 1
    assert "scheduled task" in capsys.readouterr().out


def test_run_windows_uninstall_keeps_workspace_data_by_default(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: True)
    monkeypatch.setattr(cli, "_windows_processes_in_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_purge_install_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_remove_windows_launcher_dir", lambda _link_dir: None)
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)
    monkeypatch.setattr(cli, "_remove_agent8088_searxng_container", lambda: False)
    removed_calls = []
    monkeypatch.setattr(cli, "_remove_agent8088_workspace_data", lambda: removed_calls.append(1) or 2)

    assert cli._run_windows_uninstall(home, workspace=False)
    assert removed_calls == []

    assert cli._run_windows_uninstall(home, workspace=True)
    assert removed_calls == [1]


def test_run_windows_uninstall_removes_searxng_container(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent8088"
    home.mkdir()
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: True)
    monkeypatch.setattr(cli, "_windows_processes_in_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_purge_install_tree", lambda _home: [])
    monkeypatch.setattr(cli, "_remove_windows_launcher_dir", lambda _link_dir: None)
    monkeypatch.setattr(cli, "_warn_shared_playwright_cache", lambda: None)
    monkeypatch.setattr(cli, "_remove_agent8088_searxng_container", lambda: True)

    assert cli._run_windows_uninstall(home)

    assert "Removed the SearXNG Docker container." in capsys.readouterr().out


def test_run_windows_uninstall_removes_searxng_container_even_if_home_already_gone(tmp_path, monkeypatch, capsys):
    """The container is Docker's responsibility, not the filesystem's - it must
    still get cleaned up even when $AGENT8088_HOME was already deleted by hand."""
    home = tmp_path / "agent8088"  # deliberately never created
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: True)
    monkeypatch.setattr(cli, "_remove_windows_launcher_dir", lambda _link_dir: None)
    monkeypatch.setattr(cli, "_remove_agent8088_searxng_container", lambda: True)

    assert cli._run_windows_uninstall(home)

    assert "Removed the SearXNG Docker container." in capsys.readouterr().out
