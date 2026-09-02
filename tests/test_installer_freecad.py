"""Install-FreeCAD, exercised in isolation.

Follows the extraction convention used by test_installer_hardening_round2.py:
regex one function out of install.ps1 and run it under pwsh/powershell with
its dependencies stubbed, rather than performing a real install.

The one thing worth actually proving: a failed or skipped FreeCAD install
must never abort the rest of setup -- it registers a skipped stage and returns,
same contract as every other optional component in this installer (LibreOffice,
Node.js, WhatsApp bridge). Not covered here: a real WinGet install against the
live package index -- that needs a real Windows machine with WinGet installed.

FreeCAD detection differs from LibreOffice in two ways: it checks MULTIPLE
directories (4 vs 2) and TWO executable names per directory, and it honours
the AGENT8088_FREECAD env var to allow a portable extraction.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is Windows-only")


def _powershell() -> str:
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if not ps:
        pytest.skip("PowerShell is not installed")
    return ps


def _powershell_function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$", source)
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


def _run(body: str, *functions: str) -> str:
    stubs = (
        'function Write-Info { param([string]$Message) }\n'
        'function Write-Success { param([string]$Message) Write-Host "SUCCESS:$Message" }\n'
        'function Write-Warn { param([string]$Message) Write-Host "WARN:$Message" }\n'
        'function Write-Err { param([string]$Message) Write-Host "ERR:$Message" }\n'
        'function Register-SkippedStage { param([string]$Label, [string]$Reason, [string]$Fix) '
        'Write-Host "SKIPPED:$Label|$Reason|$Fix" }\n'
    )
    script = stubs + "\n".join(_powershell_function(f) for f in functions) + "\n" + body
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", script],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


@needs_windows
def test_existing_install_is_detected_and_winget_is_never_invoked():
    """Detection-first: existing freecadcmd.exe or FreeCADCmd.exe means no reinstall.

    FreeCAD detection is more complex than LibreOffice: it checks multiple
    directories and two possible executable names (freecadcmd.exe and FreeCADCmd.exe).
    The stub must recognize both names across all checked paths.
    """
    out = _run(
        'function Test-Path { param($Path) '
        '$Path -like "*FreeCAD*bin*freecadcmd.exe" -or $Path -like "*FreeCAD*bin*FreeCADCmd.exe" }\n'
        'function Get-Command { throw "winget must not be called when freecadcmd already exists" }\n'
        '$r = Install-FreeCAD\n'
        'Write-Output "Result=$r"',
        "Install-FreeCAD",
    )
    assert "Result=True" in out
    assert "SUCCESS:FreeCAD found" in out


@needs_windows
def test_missing_winget_skips_without_aborting_install():
    """No WinGet means no silent install is possible -- warn and continue,
    never throw, matching Install-LibreOffice's own contract.

    The fix message must mention the portable .7z escape hatch and AGENT8088_FREECAD.
    """
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) $null }\n'
        '$r = Install-FreeCAD\n'
        'Write-Output "Result=$r"',
        "Install-FreeCAD",
    )
    assert "Result=False" in out
    assert "SKIPPED:FreeCAD|no WinGet available" in out
    assert "portable .7z" in out
    assert "AGENT8088_FREECAD" in out


@needs_windows
def test_winget_install_failure_registers_a_skipped_stage_not_a_throw():
    """WinGet runs but freecadcmd.exe still isn't there afterward (offline,
    package rejected, etc.) -- this must degrade, not crash the installer.
    """
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "fakewinget" } }\n'
        'function fakewinget { $global:LASTEXITCODE = 1 }\n'
        '$r = Install-FreeCAD\n'
        'Write-Output "Result=$r"',
        "Install-FreeCAD",
    )
    assert "Result=False" in out
    assert "SKIPPED:FreeCAD|WinGet install failed" in out


@needs_windows
def test_successful_winget_install_is_detected_afterward():
    out = _run(
        # FreeCAD has 12 candidates (6 directories x 2 executable names --
        # Program Files, Program Files (x86), and the real per-user
        # %LOCALAPPDATA%\Programs location the official installer actually
        # uses, confirmed against a live install). First 12 Test-Path checks
        # (pre-install) say absent; calls 13+ (post-install verification) say
        # present -- simulates winget actually landing the binary between the
        # two detection passes.
        '$script:calls = 0\n'
        'function Test-Path { param($Path) $script:calls++; $script:calls -gt 12 }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "fakewinget" } }\n'
        'function fakewinget { $global:LASTEXITCODE = 0 }\n'
        '$r = Install-FreeCAD\n'
        'Write-Output "Result=$r"',
        "Install-FreeCAD",
    )
    assert "Result=True" in out
    assert "SUCCESS:FreeCAD installed" in out


@needs_windows
def test_agent8088_freecad_env_var_is_honoured_and_checked_first():
    """AGENT8088_FREECAD is checked FIRST before the standard directories.

    Someone extracting the portable .7z must be able to point at it via env var
    without editing code. Confirm the env var path is used, and WinGet is never
    invoked because the executable is already found.
    """
    out = _run(
        '$env:AGENT8088_FREECAD = "C:\\portable\\freecad\\freecadcmd.exe"\n'
        'function Test-Path { param($Path) '
        '$Path -eq "C:\\portable\\freecad\\freecadcmd.exe" }\n'
        'function Get-Command { throw "winget must not be called when AGENT8088_FREECAD points to existing binary" }\n'
        '$r = Install-FreeCAD\n'
        'Write-Output "Result=$r"',
        "Install-FreeCAD",
    )
    assert "Result=True" in out
    assert "SUCCESS:FreeCAD found at C:\\portable\\freecad\\freecadcmd.exe" in out
