"""Install-LibreOffice, exercised in isolation.

Follows the extraction convention used by test_installer_hardening_round2.py:
regex one function out of install.ps1 and run it under pwsh/powershell with
its dependencies stubbed, rather than performing a real install.

The one thing worth actually proving: a failed or skipped LibreOffice install
must never abort the rest of setup — it registers a skipped stage and returns,
same contract as every other optional component in this installer (Node.js,
WhatsApp bridge). Not covered here: a real WinGet install against the live
package index — that needs a real Windows machine with WinGet installed.
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
    """Detection-first: an existing soffice.exe means no reinstall attempt."""
    out = _run(
        'function Test-Path { param($Path) $Path -like "*LibreOffice\\program\\soffice.exe" -and $Path -notlike "*x86*" }\n'
        'function Get-Command { throw "winget must not be called when soffice already exists" }\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Install-LibreOffice",
    )
    assert "Result=True" in out
    assert "SUCCESS:LibreOffice found" in out


@needs_windows
def test_missing_winget_skips_without_aborting_install():
    """No WinGet means no silent install is possible -- warn and continue,
    never throw, matching Install-Node-Bridge's own contract."""
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) $null }\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Install-LibreOffice",
    )
    assert "Result=False" in out
    assert "SKIPPED:LibreOffice|no WinGet available" in out
    assert "libreoffice.org/download" in out


@needs_windows
def test_winget_install_failure_registers_a_skipped_stage_not_a_throw():
    """WinGet runs but soffice.exe still isn't there afterward (offline,
    package rejected, etc.) -- this must degrade, not crash the installer."""
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "fakewinget" } }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput, $Activity) '
        '@{ ExitCode = 1; TimedOut = $false; Output = "" } }\n'
        '$TLibreOffice = 1800\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Install-LibreOffice",
    )
    assert "Result=False" in out
    assert "SKIPPED:LibreOffice|WinGet install failed" in out


@needs_windows
def test_successful_winget_install_is_detected_afterward():
    out = _run(
        # First two Test-Path checks (pre-install) say absent; the third
        # (post-install verification) says present -- simulates winget
        # actually landing the binary between the two detection passes.
        '$script:calls = 0\n'
        'function Test-Path { param($Path) $script:calls++; $script:calls -gt 2 }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "fakewinget" } }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput, $Activity) '
        '@{ ExitCode = 0; TimedOut = $false; Output = "" } }\n'
        '$TLibreOffice = 1800\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Install-LibreOffice",
    )
    assert "Result=True" in out
    assert "SUCCESS:LibreOffice installed" in out


@needs_windows
def test_winget_install_uses_the_activity_spinner_and_expected_package():
    """The LibreOffice stage must use the common animated process wrapper,
    while retaining WinGet's exact package and non-interactive flags."""
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "winget.exe" } }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput, $Activity) '
        'Write-Host "CALL:$FilePath|$($Arguments -join ";")|$TimeoutSec|$CaptureOutput|$Activity"; '
        '@{ ExitCode = 1; TimedOut = $false; Output = "" } }\n'
        '$TLibreOffice = 1800\n'
        '$null = Install-LibreOffice',
        "Install-LibreOffice",
    )
    assert "CALL:winget.exe|install;--id;TheDocumentFoundation.LibreOffice;--exact" in out
    assert "--accept-source-agreements;--accept-package-agreements;--disable-interactivity" in out
    assert "|1800|True|Downloading and installing LibreOffice" in out


@needs_windows
def test_winget_timeout_remains_optional_and_reports_the_reason():
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "winget.exe" } }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput, $Activity) '
        '@{ ExitCode = -1; TimedOut = $true; Output = "" } }\n'
        '$TLibreOffice = 1800\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Install-LibreOffice",
    )
    assert "Result=False" in out
    assert "timed out after 1800 seconds" in out
    assert "SKIPPED:LibreOffice|WinGet install failed" in out
