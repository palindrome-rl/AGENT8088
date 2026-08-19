import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from agent8088 import cli


def test_windows_uninstall_schedules_cleanup_without_moving_live_home(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    (home / "config.txt").write_text("secret", encoding="utf-8")
    environment_removed = []
    helper_calls = []
    launcher_dir = home.with_name("agent8088-launcher")
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: launcher_dir)
    monkeypatch.setattr(
        cli, "_remove_windows_user_environment",
        lambda *paths: environment_removed.extend(paths) or True,
    )
    monkeypatch.setattr(
        cli,
        "_start_windows_cleanup_helper",
        lambda target, pid: helper_calls.append((target, pid)) or tmp_path / "cleanup.log",
    )

    assert cli._run_windows_uninstall(home)

    assert home.exists()
    assert not list(tmp_path.glob("agent8088.uninstalling-*"))
    assert helper_calls == [(home, os.getpid())]
    assert environment_removed == [
        launcher_dir,
        home / "bin",
        home / "agent8088/venv/Scripts",
    ]


def test_windows_uninstall_helper_failure_leaves_live_install_and_environment(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    marker = home / "still-installed.txt"
    marker.write_text("present", encoding="utf-8")
    environment_removed = []
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home / "agent8088/venv/Scripts")
    monkeypatch.setattr(
        cli, "_remove_windows_user_environment", lambda link: environment_removed.append(link)
    )
    monkeypatch.setattr(
        cli,
        "_start_windows_cleanup_helper",
        lambda _target, _pid: (_ for _ in ()).throw(PermissionError("blocked")),
    )

    assert not cli._run_windows_uninstall(home)

    assert marker.read_text(encoding="utf-8") == "present"
    assert environment_removed == []


def test_windows_cleanup_helper_preserves_argument_boundaries(tmp_path, monkeypatch):
    target = tmp_path / "Agent Home with spaces"
    popen_calls = []
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: popen_calls.append((args, kwargs)))

    log_path = cli._start_windows_cleanup_helper(target, 12345)

    helper = next(tmp_path.glob("agent8088-uninstall-*.ps1"))
    source = helper.read_text(encoding="utf-8")
    args, _kwargs = popen_calls[0]
    assert args[args.index("-Target") + 1] == str(target)
    quarantine = Path(args[args.index("-Quarantine") + 1])
    marker = Path(args[args.index("-MarkerPath") + 1])
    assert args[args.index("-ParentPid") + 1] == "12345"
    assert args[args.index("-LogPath") + 1] == str(log_path)
    assert quarantine.parent == target.parent
    assert quarantine.name.startswith("Agent Home with spaces.uninstalling-")
    assert marker == target.with_name("Agent Home with spaces.uninstall-pending")
    assert marker.read_text(encoding="utf-8") == str(log_path)
    assert "Move-Item -LiteralPath $Target -Destination $Quarantine" in source
    assert ".StartsWith($quarantinePrefix" in source
    assert "Remove-Item -LiteralPath $cleanupPath -Recurse -Force" in source
    assert "$attempt -lt 60" in source


def test_windows_cleanup_helper_start_failure_removes_support_files(tmp_path, monkeypatch):
    target = tmp_path / "agent8088"
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cannot start")),
    )

    with pytest.raises(OSError, match="cannot start"):
        cli._start_windows_cleanup_helper(target, 12345)

    assert not target.with_name("agent8088.uninstall-pending").exists()
    assert not list(tmp_path.glob("agent8088-uninstall-*.ps1"))


def test_windows_environment_removes_only_agent8088_entries(monkeypatch):
    agent_home = Path(r"C:\Users\Example\AppData\Local\agent8088")
    link_dir = agent_home / "agent8088/venv/Scripts"
    managed_bin = agent_home / "bin"
    values = {
        "Path": (f"C:\\Tools;{managed_bin};{link_dir};C:\\Other", 2),
        "AGENT8088_CONFIG": (r"C:\old-config.txt", 1),
    }
    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_QUERY_VALUE=1,
        KEY_SET_VALUE=2,
        REG_EXPAND_SZ=2,
        OpenKey=lambda *_args: object(),
        QueryValueEx=lambda _key, name: values[name],
        SetValueEx=lambda _key, name, _reserved, kind, value: values.__setitem__(name, (value, kind)),
        DeleteValue=lambda _key, name: values.pop(name),
        CloseKey=lambda _key: None,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setenv("AGENT8088_CONFIG", r"C:\old-config.txt")

    assert cli._remove_windows_user_environment(link_dir, managed_bin)

    assert values["Path"] == (r"C:\Tools;C:\Other", 2)
    assert "AGENT8088_CONFIG" not in values
    assert "AGENT8088_CONFIG" not in os.environ


def test_windows_uninstall_rejects_shell_that_bypasses_launcher(tmp_path, monkeypatch, capsys):
    home = tmp_path / "Agent Home"
    launcher = home.with_name("Agent Home-launcher") / "agent8088.cmd"
    launcher.parent.mkdir()
    launcher.write_text("@echo off", encoding="utf-8")
    monkeypatch.delenv("AGENT8088_LINK_DIR", raising=False)

    assert not cli._require_windows_uninstall_launcher(home)

    output = capsys.readouterr().out
    assert "Uninstall has not started" in output
    assert str(launcher) in output


def test_windows_uninstall_accepts_blocking_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_LINK_DIR", str(tmp_path / "launcher"))
    assert cli._require_windows_uninstall_launcher(tmp_path / "agent8088")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows executable locking")
def test_windows_helper_moves_and_removes_locked_home_after_process_exits(tmp_path):
    home = tmp_path / "agent8088"
    stale_quarantine = tmp_path / "agent8088.uninstalling-stale"
    stale_quarantine.mkdir()
    (stale_quarantine / "leftover.txt").write_text("old", encoding="utf-8")
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "ping.exe"
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", executable)
    process = subprocess.Popen(
        [str(executable), "127.0.0.1", "-n", "30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        with pytest.raises(PermissionError):
            shutil.rmtree(home)
        log_path = cli._start_windows_cleanup_helper(home, process.pid)
        pending = home.with_name("agent8088.uninstall-pending")
        assert pending.exists()
        time.sleep(0.5)
        assert home.exists()
    finally:
        process.terminate()
        process.wait(timeout=10)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (home.exists() or pending.exists() or not log_path.exists()):
        time.sleep(0.2)

    assert not home.exists()
    assert not list(tmp_path.glob("agent8088.uninstalling-*"))
    assert not pending.exists()
    assert log_path.read_text(encoding="utf-8-sig").startswith("SUCCESS")
    log_path.unlink()
