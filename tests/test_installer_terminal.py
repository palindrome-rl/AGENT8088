import base64
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
        ("", "active", None, "True"),
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
    ("package_version", "answer", "expected", "expected_bootstrap", "expected_launch"),
    [
        (None, "n", "failed", "False", "False"),
        ("1.18.0.0", "y", "relaunched", "True", "False"),
        ("1.22.0.0", "unused", "relaunched", "False", "True"),
    ],
)
def test_legacy_host_prompts_only_when_terminal_needs_upgrade(
    package_version, answer, expected, expected_bootstrap, expected_launch
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
$TerminalBootstrap = $false
$script:bootstrapCalled = $false
$script:launchCalled = $false
function Test-SupportedTerminalHost {{ return $false }}
function Get-WindowsTerminalPackage {{ return {package} }}
function Write-Warn {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Read-Host {{ param([string]$Prompt) return '{answer}' }}
function Start-TerminalUpgradeBootstrap {{ $script:bootstrapCalled = $true; return $true }}
function Start-InstallerInWindowsTerminal {{ $script:launchCalled = $true; return $true }}
{_powershell_function('Ensure-SupportedTerminal')}
$result = Ensure-SupportedTerminal
Write-Output "$result|$script:bootstrapCalled|$script:launchCalled"
"""
    )
    assert output.splitlines()[-1] == f"{expected}|{expected_bootstrap}|{expected_launch}"


def test_terminal_relaunch_gate_runs_before_any_install_stage():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert source.index("$terminalAction = Ensure-SupportedTerminal") < source.index(
        "if (-not (Install-Uv))"
    )
    assert 'if ($terminalAction -eq "relaunched")' in source


def test_installer_never_terminates_the_calling_powershell_process():
    """The documented ``iex (irm ...)`` runs in the user's current shell.

    A top-level ``exit`` therefore kills VS Code's terminal instead of merely
    stopping the installer.  Fatal paths must return to that shell and expose
    their status through LASTEXITCODE.
    """
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    main = source.split("# Main\n# " + "-" * 76 + "\n", 1)[1]
    code = [line for line in main.splitlines()
            if not line.strip().startswith("#")]
    assert not any(re.search(r"\bexit\s+[01]\b", line) for line in code)
    status = _powershell_function("Set-InstallerExitStatus")
    assert "if ($TerminalBootstrap) { exit $ExitCode }" in status


def test_failed_disk_preflight_returns_to_iex_caller_with_status_one():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    main = source.split("# Main\n# " + "-" * 76 + "\n", 1)[1]
    encoded = base64.b64encode(main.encode("utf-16-le")).decode("ascii")
    output = _run_powershell(
        f"""
$main = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded}'))
$TerminalBootstrap = $false
{_powershell_function('Set-InstallerExitStatus')}
function Write-Banner {{ Write-Output 'banner' }}
function Write-Info {{ param([string]$Message) Write-Output $Message }}
function Test-DiskSpace {{ return $false }}
Invoke-Expression $main
Write-Output "caller-alive|$LASTEXITCODE"
"""
    )
    assert output.splitlines()[-1] == "caller-alive|1"


def test_terminal_bootstrap_failure_preserves_child_process_exit_code():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is not installed")
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            (
                "$TerminalBootstrap = $true; "
                f"{_powershell_function('Set-InstallerExitStatus')}; "
                "Set-InstallerExitStatus -ExitCode 1"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


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
$RepoSlug = 'palindrome-rl/AGENT8088'
$Agent8088Home = "C:\\Users\\O'Brien\\Agent Home"
$InstallDir = 'C:\\Agent Install'
$SkipSetup = $true
function Get-WindowsTerminalPackage {{ return [pscustomobject]@{{ InstallLocation = '' }} }}
function Get-WindowsTerminalExecutable {{ return 'C:\\mock\\wt.exe' }}
function Get-PowerShellHostExe {{ return 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' }}
function Write-Success {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Start-Process {{
    param([string]$FilePath, [object[]]$ArgumentList)
    $script:startedFile = $FilePath
    $script:terminalArguments = $ArgumentList
}}
{_powershell_function('ConvertTo-PowerShellLiteral')}
{_powershell_function('ConvertTo-EncodedPowerShellCommand')}
{_powershell_function('Get-InstallerInvocation')}
{_powershell_function('Start-InstallerInWindowsTerminal')}
$result = Start-InstallerInWindowsTerminal
$encoded = $script:terminalArguments[-1]
$launcher = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded))
Write-Output "$result|$script:startedFile"
Write-Output ($script:terminalArguments -join '|')
Write-Output $launcher
"""
    )
    assert "True|C:\\mock\\wt.exe" in output
    assert "-EncodedCommand" in output
    assert output.index("Tls12") < output.index("Invoke-RestMethod")
    assert "palindrome-rl/AGENT8088/development/install.ps1" in output
    assert "-Agent8088Home 'C:\\Users\\O''Brien\\Agent Home'" in output
    assert "-InstallDir 'C:\\Agent Install'" in output
    assert "-SkipSetup:$true" in output
    assert "agent8088-install-" not in output


def test_terminal_upgrade_runs_in_visible_external_bootstrap():
    output = _run_powershell(
        f"""
$env:SystemRoot = 'C:\\Windows'
$Branch = 'development'
$RepoSlug = 'palindrome-rl/AGENT8088'
$Agent8088Home = 'C:\\Users\\User\\AppData\\Local\\agent8088'
$InstallDir = ''
$InstallerSourceUrl = ''
$SkipSetup = $false
function Test-Path {{ param([string]$LiteralPath) return $true }}
function Get-PowerShellHostExe {{ return 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' }}
function Write-Success {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Start-Process {{
    param([string]$FilePath, [object[]]$ArgumentList)
    $script:startedFile = $FilePath
    $script:bootstrapArguments = $ArgumentList
}}
{_powershell_function('ConvertTo-PowerShellLiteral')}
{_powershell_function('ConvertTo-EncodedPowerShellCommand')}
{_powershell_function('Get-InstallerInvocation')}
{_powershell_function('Start-TerminalUpgradeBootstrap')}
$result = Start-TerminalUpgradeBootstrap
$encoded = $script:bootstrapArguments[-1]
$bootstrap = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded))
Write-Output "$result|$script:startedFile"
Write-Output $bootstrap
"""
    )
    assert "True|C:\\Windows\\System32\\conhost.exe" in output
    assert "This window will remain open" in output
    assert "Agent8088 installation could not continue" in output
    assert "Read-Host" in output


def test_windows_installer_urls_use_the_public_repository():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert '$RepoSlug = "palindrome-rl/AGENT8088"' in source
    assert "RT-Internal-DS/Agent8088-Features-added" not in source
    assert "tayyabimam1/Agent8088-Features-added" not in source


def test_terminal_bootstrap_installs_then_launches():
    output = _run_powershell(
        f"""
$WindowsTerminalMinVersion = [version]'1.19.0.0'
$NonInteractive = $false
$TerminalBootstrap = $true
$script:installCalled = $false
$script:launchCalled = $false
function Test-SupportedTerminalHost {{ return $false }}
function Get-WindowsTerminalPackage {{ return $null }}
function Write-Warn {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Install-WindowsTerminal {{ param($ExistingPackage); $script:installCalled = $true; return $true }}
function Start-InstallerInWindowsTerminal {{ $script:launchCalled = $true; return $true }}
{_powershell_function('Ensure-SupportedTerminal')}
$result = Ensure-SupportedTerminal
Write-Output "$result|$script:installCalled|$script:launchCalled"
"""
    )
    assert output.splitlines()[-1] == "relaunched|True|True"


def test_winget_no_applicable_update_accepts_a_working_terminal_alias():
    output = _run_powershell(
        f"""
$WindowsTerminalMinVersion = [version]'1.19.0.0'
function fakewinget {{ $global:LASTEXITCODE = -1978335189 }}
function Get-Command {{ return [pscustomobject]@{{ Source = 'fakewinget' }} }}
function Get-WindowsTerminalPackage {{ return $null }}
function Get-WindowsTerminalExecutable {{ return 'C:\\Users\\User\\AppData\\Local\\Microsoft\\WindowsApps\\wt.exe' }}
function Write-Info {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Success {{ param([string]$Message) }}
{_powershell_function('Install-WindowsTerminal')}
Write-Output (Install-WindowsTerminal $null)
"""
    )
    assert output.splitlines()[-1] == "True"
