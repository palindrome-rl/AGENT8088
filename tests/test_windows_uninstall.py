import base64
import os
import shutil
import stat
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from agent8088 import cli


def _encoded_script(args):
    return base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16-le")


def test_windows_uninstall_deletes_what_it_can_and_defers_the_rest(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    (home / "node").mkdir(parents=True)
    (home / "node" / "node.exe").write_text("binary", encoding="utf-8")
    (home / "config.txt").write_text("secret", encoding="utf-8")
    locked = home / "agent8088"
    locked.mkdir()
    environment_removed = []
    helper_calls = []
    launcher_dir = home.with_name("agent8088-launcher")
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: launcher_dir)
    monkeypatch.setattr(
        cli, "_remove_windows_user_environment",
        lambda *paths: environment_removed.extend(paths) or True,
    )
    # Stand in for the executable Windows keeps locked inside the install.
    monkeypatch.setattr(cli, "_purge_install_tree", lambda _target: [locked])
    monkeypatch.setattr(
        cli,
        "_start_windows_cleanup_helper",
        lambda target, pid: helper_calls.append((target, pid)) or tmp_path / "cleanup.log",
    )

    assert cli._run_windows_uninstall(home)

    assert helper_calls == [(home, os.getpid())]
    assert environment_removed == [
        launcher_dir,
        home / "bin",
        home / "agent8088/venv/Scripts",
    ]


def test_windows_uninstall_skips_the_helper_when_nothing_is_locked(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    (home / "node").mkdir(parents=True)
    (home / "config.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home.with_name("agent8088-launcher"))
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: True)
    monkeypatch.setattr(
        cli,
        "_start_windows_cleanup_helper",
        lambda *_args: pytest.fail("cleanup must not be deferred when the tree is already gone"),
    )

    assert cli._run_windows_uninstall(home)
    assert not home.exists()


def test_purge_install_tree_removes_contents_and_reports_survivors(tmp_path):
    home = tmp_path / "agent8088"
    (home / "node" / "deep").mkdir(parents=True)
    (home / "node" / "deep" / "file.txt").write_text("x", encoding="utf-8")
    (home / "config.txt").write_text("x", encoding="utf-8")

    assert cli._purge_install_tree(home) == []
    assert not home.exists()
    assert cli._purge_install_tree(home) == []


def test_windows_uninstall_stops_when_the_environment_cannot_be_updated(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    marker = home / "still-installed.txt"
    marker.write_text("present", encoding="utf-8")
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home / "agent8088/venv/Scripts")
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: None)
    monkeypatch.setattr(
        cli,
        "_start_windows_cleanup_helper",
        lambda *_args: pytest.fail("files must not be touched once the environment fails"),
    )

    assert not cli._run_windows_uninstall(home)
    assert marker.read_text(encoding="utf-8") == "present"


def test_windows_uninstall_reports_failure_when_cleanup_cannot_be_scheduled(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    home.mkdir()
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home.with_name("agent8088-launcher"))
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: True)
    monkeypatch.setattr(cli, "_purge_install_tree", lambda _target: [home / "locked.exe"])
    monkeypatch.setattr(
        cli,
        "_start_windows_cleanup_helper",
        lambda _target, _pid: (_ for _ in ()).throw(PermissionError("blocked")),
    )

    assert not cli._run_windows_uninstall(home)


def test_windows_cleanup_helper_passes_paths_without_a_script_on_disk(tmp_path, monkeypatch):
    target = tmp_path / "Agent Home with spaces"
    popen_calls = []
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: popen_calls.append((args, kwargs)))

    log_path = cli._start_windows_cleanup_helper(target, 12345)

    args, _kwargs = popen_calls[0]
    source = _encoded_script(args)
    marker = target.with_name("Agent Home with spaces.uninstall-pending")
    # -EncodedCommand is exempt from the script execution policy, and leaves no
    # temp script behind that could fail to be written or deleted.
    assert "-File" not in args
    assert not list(tmp_path.glob("*.ps1"))
    assert f"$Target = '{target}'" in source
    assert f"$LogPath = '{log_path}'" in source
    assert f"$MarkerPath = '{marker}'" in source
    assert "$ParentPid = 12345" in source
    assert marker.read_text(encoding="utf-8") == str(log_path)
    quarantine_line = next(
        line for line in source.splitlines() if line.startswith("$Quarantine = ")
    )
    quarantine = Path(quarantine_line.split(" = ", 1)[1].strip("'"))
    assert quarantine.parent == target.parent
    assert quarantine.name.startswith("Agent Home with spaces.uninstalling-")
    # The rename is best effort; deleting in place is what has to happen.
    assert "Move-Item -LiteralPath $Target -Destination $Quarantine" in source
    assert "rd /s /q" in source
    assert "Remove-Item -LiteralPath $MarkerPath" in source
    # No .NET calls: those are blocked under Constrained Language Mode.
    assert "[IO." not in source
    # A BOM in the log renders as mojibake when the launcher `type`s it.
    assert "-Value $Message -Encoding Default" in source


def test_windows_cleanup_helper_escapes_quotes_in_paths(tmp_path, monkeypatch):
    target = tmp_path / "d'arcy" / "agent8088"
    target.parent.mkdir()
    popen_calls = []
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: popen_calls.append((args, kwargs)))

    cli._start_windows_cleanup_helper(target, 999)

    source = _encoded_script(popen_calls[0][0])
    assert f"$Target = '{str(target).replace(chr(39), chr(39) * 2)}'" in source


def test_windows_cleanup_helper_start_failure_removes_the_marker(tmp_path, monkeypatch):
    target = tmp_path / "agent8088"
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cannot start")),
    )

    with pytest.raises(OSError, match="cannot start"):
        cli._start_windows_cleanup_helper(target, 12345)

    assert not target.with_name("agent8088.uninstall-pending").exists()


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


def test_launcher_directory_is_left_to_the_launcher_that_started_us(tmp_path, monkeypatch):
    link_dir = tmp_path / "agent8088-launcher"
    link_dir.mkdir()
    (link_dir / "agent8088.cmd").write_text("@echo off", encoding="utf-8")
    monkeypatch.setenv("AGENT8088_LINK_DIR", str(link_dir))

    # Deleting a running batch file makes cmd fail on its next line, so the
    # launcher deletes itself once it is finished instead.
    assert not cli._remove_windows_launcher_dir(link_dir)
    assert link_dir.exists()

    monkeypatch.delenv("AGENT8088_LINK_DIR")
    assert cli._remove_windows_launcher_dir(link_dir)
    assert not link_dir.exists()


def test_processes_in_tree_parses_the_listing_and_excludes_this_process(monkeypatch):
    home = Path(r"C:\Users\Example\AppData\Local\agent8088")
    monkeypatch.setattr(
        cli, "_run_powershell_capture",
        lambda script, **_kw: f"27260\tagent8088.exe\r\n12496\tpython.exe\r\n{os.getpid()}\tself.exe\r\n",
    )

    assert cli._windows_processes_in_tree(home) == [(27260, "agent8088.exe"), (12496, "python.exe")]


def test_processes_in_tree_survives_a_powershell_that_says_nothing(monkeypatch):
    monkeypatch.setattr(cli, "_run_powershell_capture", lambda _script, **_kw: None)
    assert cli._windows_processes_in_tree(Path(r"C:\agent8088")) == []
    monkeypatch.setattr(cli, "_run_powershell_capture", lambda _script, **_kw: "garbage\r\n\r\n")
    assert cli._windows_processes_in_tree(Path(r"C:\agent8088")) == []


def test_processes_in_tree_asks_only_about_the_install_directory(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli, "_run_powershell_capture",
        lambda script, **_kw: seen.setdefault("script", script) and "",
    )
    cli._windows_processes_in_tree(Path(r"C:\Users\d'arcy\agent8088"))
    # The quote has to be doubled or the snippet will not parse.
    assert "$prefix = 'C:\\Users\\d''arcy\\agent8088\\*'" in seen["script"]
    assert "-ilike $prefix" in seen["script"]
    # Module lists are only pulled for plausible hosts; doing it for every
    # process on the box costs about a minute.
    assert "$hosts = @('python.exe', 'pythonw.exe', 'node.exe', 'agent8088.exe')" in seen["script"]
    # This process and its ancestors must be walked and skipped: the launcher
    # chain runs from inside the install, and a tree kill on an ancestor stops
    # the uninstall itself.
    assert f"$selfPid = {os.getpid()}" in seen["script"]
    assert "$walk = [int]$byId[$walk].ParentProcessId" in seen["script"]
    assert "if ($mine.ContainsKey([int]$proc.ProcessId)) { continue }" in seen["script"]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows process ancestry")
def test_processes_in_tree_never_reports_this_process_or_its_launcher(tmp_path):
    """A tree kill on an ancestor would stop the uninstall mid-run."""
    home = tmp_path / "agent8088"
    home.mkdir()
    # Every ancestor of this test, reported as if it ran from inside the install.
    listing = cli._run_powershell_capture(
        f"$selfPid = {os.getpid()}\n"
        "$byId = @{}\n"
        "foreach ($proc in @(Get-CimInstance Win32_Process)) { $byId[[int]$proc.ProcessId] = $proc }\n"
        "$walk = [int]$selfPid\n"
        "for ($step = 0; $step -lt 64; $step++) {\n"
        "  if (-not $walk) { break }\n"
        "  if (-not $byId.ContainsKey($walk)) { break }\n"
        "  \"$walk\"\n"
        "  $walk = [int]$byId[$walk].ParentProcessId\n"
        "}\n"
    )
    ancestors = {int(line) for line in (listing or "").split() if line.strip().isdigit()}
    assert os.getpid() in ancestors, "the walk should at least find this process"

    # Point the scan at a directory that holds nothing, so any hit is a bug.
    reported = {pid for pid, _name in cli._windows_processes_in_tree(home)}
    assert not (reported & ancestors), reported & ancestors


def test_windows_uninstall_stops_blocking_processes_before_deleting(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent8088"
    home.mkdir()
    order = []
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home.with_name("agent8088-launcher"))
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *_paths: True)
    monkeypatch.setattr(cli, "_windows_processes_in_tree", lambda _t: [(27260, "agent8088.exe")])
    monkeypatch.setattr(
        cli, "_stop_windows_processes",
        lambda procs: order.append(("stop", procs)) or len(procs),
    )
    monkeypatch.setattr(
        cli, "_purge_install_tree", lambda _t: order.append(("purge", None)) or [],
    )

    assert cli._run_windows_uninstall(home)

    assert [step for step, _ in order] == ["stop", "purge"]
    output = capsys.readouterr().out
    assert "agent8088.exe (pid 27260)" in output
    assert "Windows cannot delete a running program" in output


@pytest.mark.skipif(os.name != "nt", reason="requires Windows process image paths")
def test_windows_finds_and_stops_a_process_running_from_the_install(tmp_path):
    """The real blocker: an executable running from inside the install tree."""
    home = tmp_path / "agent8088"
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "ping.exe"
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", executable)
    process = subprocess.Popen(
        [str(executable), "127.0.0.1", "-n", "60"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        found = cli._windows_processes_in_tree(home)
        assert (process.pid, "ping.exe") in found, found
        # Nothing outside the tree may be swept up with it.
        assert all(pid == process.pid for pid, _name in found), found
        assert cli._stop_windows_processes(found) == 1
        process.wait(timeout=15)
        assert cli._purge_install_tree(home) == []
        assert not home.exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows executable locking")
def test_windows_helper_removes_locked_home_after_the_process_exits(tmp_path):
    home = tmp_path / "agent8088"
    stale_quarantine = tmp_path / "agent8088.uninstalling-stale"
    stale_quarantine.mkdir()
    (stale_quarantine / "leftover.txt").write_text("old", encoding="utf-8")
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "ping.exe"
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", executable)
    readonly = home / "config.txt"
    readonly.write_text("keep", encoding="utf-8")
    os.chmod(readonly, stat.S_IREAD)
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

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and (home.exists() or pending.exists() or not log_path.exists()):
        time.sleep(0.2)

    assert not home.exists()
    assert not list(tmp_path.glob("agent8088.uninstalling-*"))
    # The marker is cleared however the run ended, so it can never wedge the
    # next uninstall or the next install.
    assert not pending.exists()
    assert log_path.read_text(encoding="utf-8-sig").startswith("SUCCESS")
    log_path.unlink()
