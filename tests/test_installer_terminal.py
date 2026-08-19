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


@pytest.mark.parametrize(
    ("term_program", "wt_session", "package_version", "expected"),
    [
        ("vscode", "", None, "True"),
        ("", "active", "1.19.0.0", "True"),
        ("", "active", "1.18.9999.0", "False"),
        ("", "", "1.22.0.0", "False"),
    ],
)
def test_windows_terminal_host_support_is_detected(
    term_program, wt_session, package_version, expected
):
    package = (
        f"[pscustomobject]@{{ Version = '{package_version}' }}"
        if package_version
        else "$null"
    )
    output = _run_powershell(
        f"""
$env:TERM_PROGRAM = '{term_program}'
$env:WT_SESSION = '{wt_session}'
$WindowsTerminalMinVersion = [version]'1.19.0.0'
function Get-WindowsTerminalPackage {{ return {package} }}
{_powershell_function('Test-SupportedTerminalHost')}
Write-Output (Test-SupportedTerminalHost)
"""
    )
    assert output.splitlines()[-1] == expected


@pytest.mark.parametrize(
    ("package_version", "answer", "expected", "expected_install", "expected_launch"),
    [
        (None, "n", "failed", "False", "False"),
        ("1.18.0.0", "y", "relaunched", "True", "True"),
        ("1.22.0.0", "unused", "relaunched", "False", "True"),
    ],
)
def test_legacy_host_prompts_only_when_terminal_needs_upgrade(
    package_version, answer, expected, expected_install, expected_launch
):
    package = (
        f"[pscustomobject]@{{ Version = '{package_version}' }}"
        if package_version
        else "$null"
    )
    output = _run_powershell(
        f"""
$WindowsTerminalMinVersion = [version]'1.19.0.0'
$NonInteractive = $false
$script:installCalled = $false
$script:launchCalled = $false
function Test-SupportedTerminalHost {{ return $false }}
function Get-WindowsTerminalPackage {{ return {package} }}
function Write-Warn {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Read-Host {{ param([string]$Prompt) return '{answer}' }}
function Install-WindowsTerminal {{ param($ExistingPackage); $script:installCalled = $true; return $true }}
function Start-InstallerInWindowsTerminal {{ $script:launchCalled = $true; return $true }}
{_powershell_function('Ensure-SupportedTerminal')}
$result = Ensure-SupportedTerminal
Write-Output "$result|$script:installCalled|$script:launchCalled"
"""
    )
    assert output.splitlines()[-1] == f"{expected}|{expected_install}|{expected_launch}"


def test_terminal_relaunch_gate_runs_before_any_install_stage():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert source.index("$terminalAction = Ensure-SupportedTerminal") < source.index(
        "if (-not (Install-Uv))"
    )
    assert 'if ($terminalAction -eq "relaunched") { exit 0 }' in source


def test_powershell_literal_escapes_single_quotes():
    output = _run_powershell(
        f"""
{_powershell_function('ConvertTo-PowerShellLiteral')}
Write-Output (ConvertTo-PowerShellLiteral "C:\\Users\\O'Brien")
"""
    )
    assert output.splitlines()[-1] == "'C:\\Users\\O''Brien'"


def test_terminal_relaunch_preserves_installer_parameters():
    output = _run_powershell(
        f"""
$Branch = 'development'
$Agent8088Home = "C:\\Users\\O'Brien\\Agent Home"
$InstallDir = 'C:\\Agent Install'
$SkipSetup = $true
function Get-WindowsTerminalPackage {{ return [pscustomobject]@{{ InstallLocation = '' }} }}
function Get-Command {{ return [pscustomobject]@{{ Source = 'C:\\mock\\wt.exe' }} }}
function Get-PowerShellHostExe {{ return 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' }}
function Write-Success {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Start-Process {{
    param([string]$FilePath, [object[]]$ArgumentList)
    $script:startedFile = $FilePath
    $script:launcherPath = $ArgumentList[-1].Trim('"')
}}
{_powershell_function('ConvertTo-PowerShellLiteral')}
{_powershell_function('Start-InstallerInWindowsTerminal')}
$result = Start-InstallerInWindowsTerminal
$launcher = Get-Content -LiteralPath $script:launcherPath -Raw
Remove-Item -LiteralPath $script:launcherPath -Force
Write-Output "$result|$script:startedFile"
Write-Output $launcher
"""
    )
    assert "True|C:\\mock\\wt.exe" in output
    assert "AGENT8088/development/install.ps1" in output
    assert "-Agent8088Home 'C:\\Users\\O''Brien\\Agent Home'" in output
    assert "-InstallDir 'C:\\Agent Install'" in output
    assert "-SkipSetup:$true" in output
