import re
import shutil
import subprocess
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
{_powershell_function('Wait-ForPendingUninstall')}
Write-Output (Wait-ForPendingUninstall)
"""
    )
    assert output.splitlines()[-1] == "True"
    assert not pending.exists()


def test_windows_installer_stops_for_unfinished_pending_uninstall(tmp_path):
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
{_powershell_function('Wait-ForPendingUninstall')}
Write-Output (Wait-ForPendingUninstall)
"""
    )
    assert "A previous Agent8088 uninstall has not completed." in output
    assert f"Cleanup log: {cleanup_log}" in output
    assert output.splitlines()[-1] == "False"


def test_windows_installer_requires_verified_repository():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    clone = _powershell_function("Clone-Repo")
    assert 'if (-not (Clone-Repo)) { exit 1 }' in source
    assert source.index('if (-not (Wait-ForPendingUninstall))') < source.index('if (-not (Install-Uv))')
    assert 'Repository verification failed' in clone
    assert 'installedCommit = "unknown"' not in clone
