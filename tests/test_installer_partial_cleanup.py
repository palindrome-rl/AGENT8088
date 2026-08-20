import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _powershell_function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$", source)
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


def _run_powershell(command: str) -> str:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is not installed")
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_windows_installer_removes_partial_directory(tmp_path):
    partial = tmp_path / "agent8088"
    partial.mkdir()
    (partial / "leftover.txt").write_text("partial", encoding="utf-8")
    output = _run_powershell(
        f"""
$InstallDir = '{str(partial).replace("'", "''")}'
function Write-Err {{ param([string]$Message) }}
{_powershell_function('Remove-IncompleteInstallDirectory')}
Write-Output (Remove-IncompleteInstallDirectory)
"""
    )
    assert output.splitlines()[-1] == "True"
    assert not partial.exists()


def test_windows_installer_stops_when_partial_directory_stays_locked():
    output = _run_powershell(
        f"""
$InstallDir = 'C:\\locked-agent8088'
function Write-Err {{ param([string]$Message) }}
function Test-Path {{ param([string]$LiteralPath) return $true }}
function Remove-Item {{ throw 'file is locked' }}
function Start-Sleep {{ param([int]$Seconds) }}
{_powershell_function('Remove-IncompleteInstallDirectory')}
Write-Output (Remove-IncompleteInstallDirectory)
"""
    )
    assert output.splitlines()[-1] == "False"


def test_windows_installer_continues_without_pending_uninstall(tmp_path):
    agent_root = tmp_path / "agent8088"
    output = _run_powershell(
        f"""
$Agent8088Home = '{str(agent_root).replace("'", "''")}'
$PendingUninstallWaitSeconds = 0
function Write-Info {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Warn {{ param([string]$Message) }}
{_powershell_function('Wait-ForPendingUninstall')}
Write-Output (Wait-ForPendingUninstall)
"""
    )
    assert output.splitlines()[-1] == "True"


def test_windows_installer_clears_pending_marker_after_home_moves(tmp_path):
    agent_root = tmp_path / "agent8088"
    pending = tmp_path / "agent8088.uninstall-pending"
    pending.write_text(str(tmp_path / "cleanup.log"), encoding="utf-8")
    output = _run_powershell(
        f"""
$Agent8088Home = '{str(agent_root).replace("'", "''")}'
$PendingUninstallWaitSeconds = 0
function Write-Info {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Warn {{ param([string]$Message) }}
{_powershell_function('Wait-ForPendingUninstall')}
Write-Output (Wait-ForPendingUninstall)
"""
    )
    assert output.splitlines()[-1] == "True"
    assert not pending.exists()


def test_windows_installer_clears_a_stale_pending_marker_and_continues(tmp_path):
    """A marker left by a helper that died must not lock people out of installing."""
    agent_root = tmp_path / "agent8088"
    agent_root.mkdir()
    pending = tmp_path / "agent8088.uninstall-pending"
    cleanup_log = tmp_path / "cleanup.log"
    pending.write_text(str(cleanup_log), encoding="utf-8")
    output = _run_powershell(
        f"""
$Agent8088Home = '{str(agent_root).replace("'", "''")}'
$PendingUninstallWaitSeconds = 0
function Write-Info {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) Write-Output $Message }}
function Write-Warn {{ param([string]$Message) Write-Output $Message }}
{_powershell_function('Wait-ForPendingUninstall')}
Write-Output (Wait-ForPendingUninstall)
"""
    )
    assert "A previous Agent8088 uninstall did not finish" in output
    assert f"Cleanup log: {cleanup_log}" in output
    assert output.splitlines()[-1] == "True"
    assert not pending.exists()


def test_windows_installer_requires_verified_repository():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    clone = _powershell_function("Clone-Repo")
    assert 'if (-not (Clone-Repo)) {' in source
    clone_failure = source.split('if (-not (Clone-Repo)) {', 1)[1].split('}', 1)[0]
    assert 'Set-InstallerExitStatus -ExitCode 1' in clone_failure
    assert 'return' in clone_failure
    assert source.index('if (-not (Wait-ForPendingUninstall))') < source.index(
        "$terminalAction = Ensure-SupportedTerminal"
    )
    assert source.index('if (-not (Wait-ForPendingUninstall))') < source.index('if (-not (Install-Uv))')
    assert 'Repository verification failed' in clone
    assert 'installedCommit = "unknown"' not in clone


def test_windows_launcher_wait_is_bounded_and_ends_on_a_deleted_home():
    """The launcher must not sit there silently while a stuck helper spins."""
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    launcher = _powershell_function("Write-Agent8088Launcher")
    assert "echo Finishing Agent8088 cleanup..." in launcher
    # An install that is already gone ends the wait, whatever the marker says.
    assert launcher.index('if not exist "%_agent8088_home%" goto agent8088_uninstall_finished') < (
        launcher.index("if %_agent8088_attempts% GEQ")
    )
    cap = int(re.search(r"if %_agent8088_attempts% GEQ (\d+)", launcher).group(1))
    assert cap <= 60, "a longer wait than the cleanup helper can even take reads as a hang"
    assert source.count("_agent8088_attempts") >= 3


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd launcher behavior")
def test_windows_launcher_does_not_wait_when_the_uninstall_was_cancelled(tmp_path):
    """A cancelled uninstall must not wait on a marker it never wrote."""
    agent_root = tmp_path / "agent8088"
    agent_root.mkdir()
    launcher_dir = tmp_path / "agent8088-launcher"
    marker = tmp_path / "agent8088.uninstall-pending"
    marker.write_text(str(tmp_path / "cleanup.log"), encoding="utf-8")
    declining_agent = tmp_path / "declining-agent.cmd"
    crlf = chr(13) + chr(10)
    declining_agent.write_text(
        f"@echo Uninstall cancelled.{crlf}@exit /b 1{crlf}", encoding="ascii"
    )

    _run_powershell(
        f"""
$Agent8088Home = '{str(agent_root).replace("'", "''")}'
$LauncherDir = '{str(launcher_dir).replace("'", "''")}'
$InstallDir = '{str(tmp_path).replace("'", "''")}'
function Write-Err {{ param([string]$Message) Write-Output $Message }}
{_powershell_function('Write-Agent8088Launcher')}
Write-Output (Write-Agent8088Launcher -AgentExe '{str(declining_agent).replace("'", "''")}')
"""
    )
    started = time.monotonic()
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(launcher_dir / "agent8088.cmd"), "--uninstall"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert time.monotonic() - started < 10, result.stdout
    assert result.returncode == 1
    assert "Uninstall cancelled." in result.stdout
    assert "Finishing Agent8088 cleanup" not in result.stdout
    assert "did not remove every file" not in result.stdout
    assert marker.exists()
    assert agent_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd launcher behavior")
def test_windows_launcher_finishes_when_the_marker_outlives_the_install(tmp_path):
    launcher_dir = tmp_path / "agent8088-launcher"
    marker = tmp_path / "agent8088.uninstall-pending"
    marker.write_text(str(tmp_path / "cleanup.log"), encoding="utf-8")
    fake_agent = Path(os.environ["SystemRoot"]) / "System32" / "attrib.exe"

    _run_powershell(
        f"""
$Agent8088Home = '{str(tmp_path / "agent8088").replace("'", "''")}'
$LauncherDir = '{str(launcher_dir).replace("'", "''")}'
$InstallDir = '{str(tmp_path).replace("'", "''")}'
function Write-Err {{ param([string]$Message) Write-Output $Message }}
{_powershell_function('Write-Agent8088Launcher')}
Write-Output (Write-Agent8088Launcher -AgentExe '{str(fake_agent).replace("'", "''")}')
"""
    )
    started = time.monotonic()
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(launcher_dir / "agent8088.cmd"), "--uninstall"],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert time.monotonic() - started < 15, result.stdout
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK] Agent8088 was completely uninstalled." in result.stdout
    assert not launcher_dir.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd launcher behavior")
def test_windows_launcher_waits_for_complete_uninstall_and_removes_itself(tmp_path):
    agent_root = tmp_path / "agent8088"
    launcher_dir = tmp_path / "agent8088-launcher"
    agent_root.mkdir()
    marker = tmp_path / "agent8088.uninstall-pending"
    marker.write_text(str(tmp_path / "cleanup.log"), encoding="utf-8")
    fake_agent = Path(os.environ["SystemRoot"]) / "System32" / "attrib.exe"

    output = _run_powershell(
        f"""
$Agent8088Home = '{str(agent_root).replace("'", "''")}'
$LauncherDir = '{str(launcher_dir).replace("'", "''")}'
$InstallDir = '{str(tmp_path).replace("'", "''")}'
function Write-Err {{ param([string]$Message) Write-Output $Message }}
{_powershell_function('Write-Agent8088Launcher')}
Write-Output (Write-Agent8088Launcher -AgentExe '{str(fake_agent).replace("'", "''")}')
"""
    )
    assert output.splitlines()[-1] == "True"
    launcher = launcher_dir / "agent8088.cmd"

    def finish_cleanup():
        time.sleep(1.2)
        shutil.rmtree(agent_root)
        marker.unlink()

    cleanup = threading.Thread(target=finish_cleanup)
    cleanup.start()
    started = time.monotonic()
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(launcher), "--uninstall"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    elapsed = time.monotonic() - started
    cleanup.join(timeout=5)

    assert result.returncode == 0, result.stdout + result.stderr
    assert elapsed >= 1
    assert "[OK] Agent8088 was completely uninstalled." in result.stdout
    assert not agent_root.exists()
    assert not launcher_dir.exists()
