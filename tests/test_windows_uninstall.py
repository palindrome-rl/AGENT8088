import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from agent8088 import cli


def test_windows_uninstall_quarantines_before_deleting(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    (home / "config.txt").write_text("secret", encoding="utf-8")
    environment_removed = []
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home / "agent8088/venv/Scripts")
    monkeypatch.setattr(
        cli, "_remove_windows_user_environment", lambda link: environment_removed.append(link) or True
    )

    assert cli._run_windows_uninstall(home, lambda *_: None)

    assert not home.exists()
    assert not list(tmp_path.glob("agent8088.uninstalling-*"))
    assert environment_removed == [home / "agent8088/venv/Scripts"]


def test_windows_uninstall_rename_failure_leaves_live_install_untouched(tmp_path, monkeypatch):
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
        cli, "_quarantine_windows_home", lambda _home: (_ for _ in ()).throw(PermissionError("locked"))
    )

    assert not cli._run_windows_uninstall(home, lambda *_: None)

    assert marker.read_text(encoding="utf-8") == "present"
    assert environment_removed == []


def test_windows_uninstall_defers_only_quarantined_cleanup(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    quarantine = tmp_path / "agent8088.uninstalling-test"
    home.mkdir()
    helper_calls = []
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home / "agent8088/venv/Scripts")
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda _link: False)
    monkeypatch.setattr(cli, "_quarantine_windows_home", lambda _home: quarantine)
    monkeypatch.setattr(shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(
        cli,
        "_start_windows_cleanup_helper",
        lambda target, pid: helper_calls.append((target, pid)) or tmp_path / "cleanup.log",
    )

    assert cli._run_windows_uninstall(home, lambda *_: None)

    assert helper_calls == [(quarantine, os.getpid())]


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
    assert args[args.index("-ParentPid") + 1] == "12345"
    assert args[args.index("-LogPath") + 1] == str(log_path)
    assert "Remove-Item -LiteralPath $Target -Recurse -Force" in source
    assert "$attempt -lt 60" in source


def test_windows_environment_removes_only_agent8088_entries(monkeypatch):
    link_dir = Path(r"C:\Users\Example\AppData\Local\agent8088\agent8088\venv\Scripts")
    values = {
        "Path": (f"C:\\Tools;{link_dir};C:\\Other", 2),
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

    assert cli._remove_windows_user_environment(link_dir)

    assert values["Path"] == (r"C:\Tools;C:\Other", 2)
    assert "AGENT8088_CONFIG" not in values
    assert "AGENT8088_CONFIG" not in os.environ


@pytest.mark.skipif(os.name != "nt", reason="requires Windows executable locking")
def test_windows_helper_removes_locked_quarantine_after_process_exits(tmp_path):
    home = tmp_path / "agent8088"
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
        quarantine = cli._quarantine_windows_home(home)
        with pytest.raises(PermissionError):
            shutil.rmtree(quarantine)
        log_path = cli._start_windows_cleanup_helper(quarantine, process.pid)
    finally:
        process.terminate()
        process.wait(timeout=10)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (quarantine.exists() or not log_path.exists()):
        time.sleep(0.2)

    assert not quarantine.exists()
    assert log_path.read_text(encoding="utf-8-sig").startswith("SUCCESS")
    log_path.unlink()
