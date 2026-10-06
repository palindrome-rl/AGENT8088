import base64
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Every assertion in this module is about the Windows installer path
# (conhost.exe, WindowsPowerShell\\v1.0). There was no platform guard, so
# the suite failed on macOS/Linux for a test that cannot apply there.
pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="Windows installer behaviour")


ROOT = Path(__file__).resolve().parents[2]


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


def test_windows_installer_urls_use_the_public_repository():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert '$RepoSlug = "palindrome-rl/AGENT8088"' in source
    assert "tayyabimam1/Agent8088-Features-added" not in source


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

# --- Terminal handoff: no -EncodedCommand, an escape hatch, honest messages ---
#
# A manager's Windows 10 machine (no Windows Terminal) stopped at "This script
# contains malicious content and has been blocked by your antivirus software".
# The setup helper was launched with -EncodedCommand whose decoded text held a
# second, nested -EncodedCommand -- the shape antivirus products flag on sight.
# The handoff now writes plain text to a one-time file and runs it with
# -Command, which, unlike a .ps1 file, machine execution policy cannot block.


def _handoff_stubs():
    return """
function Write-Success { param([string]$Message) }
function Write-Err { param([string]$Message) }
function Write-Info { param([string]$Message) }
function Get-PowerShellHostExe { return 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' }
function Start-Process {
    param([string]$FilePath, [object[]]$ArgumentList)
    $script:startedFile = $FilePath
    $script:startedArguments = $ArgumentList
}
"""


def test_terminal_handoff_never_uses_encoded_commands():
    for name in ("Start-InstallerInWindowsTerminal", "Start-TerminalUpgradeBootstrap",
                 "New-HandoffCommand"):
        assert "EncodedCommand" not in _powershell_function(name), name


def test_handoff_command_runs_the_text_once_and_cleans_up(tmp_path):
    # A path with a space and an apostrophe, the two things that break quoting.
    base = tmp_path / "O'Brien Home"
    base.mkdir()
    out = base / "ran.txt"
    output = _run_powershell(
        f"""
$env:TEMP = '{str(base).replace("'", "''")}'
$env:TMP = $env:TEMP
{_powershell_function('ConvertTo-PowerShellLiteral')}
{_powershell_function('New-HandoffCommand')}
$command = New-HandoffCommand -Command "'it works; with quotes' | Set-Content -LiteralPath '{str(out).replace("'", "''")}'"
Write-Output "semicolons:" ($command.Contains(';'))
Write-Output "files-before:" @(Get-ChildItem -LiteralPath (Join-Path $env:TEMP 'agent8088-handoff') -File).Count
$ps = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'
& $ps -NoProfile -ExecutionPolicy Bypass -Command $command
Write-Output "files-after:" @(Get-ChildItem -LiteralPath (Join-Path $env:TEMP 'agent8088-handoff') -File).Count
"""
    )
    lines = output.splitlines()
    assert lines[lines.index("semicolons:") + 1] == "False"  # wt.exe splits arguments on ';'
    assert lines[lines.index("files-before:") + 1] == "1"
    assert lines[lines.index("files-after:") + 1] == "0"      # the window deleted it
    assert out.read_text().strip() == "it works; with quotes"


def test_terminal_relaunch_preserves_installer_parameters(tmp_path):
    output = _run_powershell(
        f"""
$env:TEMP = '{str(tmp_path).replace("'", "''")}'
$env:TMP = $env:TEMP
$Branch = 'AGENT8088-v1.2'
$RepoSlug = 'palindrome-rl/AGENT8088'
$Agent8088Home = "C:\\Users\\O'Brien\\Agent Home"
$InstallDir = 'C:\\Agent Install'
$InstallerSourceUrl = ''
$SkipSetup = $true
function Get-WindowsTerminalPackage {{ return [pscustomobject]@{{ InstallLocation = '' }} }}
function Get-WindowsTerminalExecutable {{ return 'C:\\mock\\wt.exe' }}
{_handoff_stubs()}
{_powershell_function('ConvertTo-PowerShellLiteral')}
{_powershell_function('New-HandoffCommand')}
{_powershell_function('Get-InstallerInvocation')}
{_powershell_function('New-HandoffMarker')}
{_powershell_function('Get-HandoffTimeoutSeconds')}
{_powershell_function('Write-HandoffFallbackHelp')}
# The launch is stubbed, so nothing writes the started marker; the handshake
# itself is covered in test_installer_terminal_handoff_confirm.py.
function Wait-HandoffStarted {{ param([string]$Marker, [int]$TimeoutSeconds) return $true }}
{_powershell_function('Start-InstallerInWindowsTerminal')}
$result = Start-InstallerInWindowsTerminal
$last = $script:startedArguments[-1]
$file = [regex]::Match($last, "ReadAllText\\('((?:[^']|'')*)'\\)").Groups[1].Value.Replace("''", "'")
Write-Output "$result|$script:startedFile"
Write-Output ($script:startedArguments -join '|')
Write-Output "-----"
Write-Output ([IO.File]::ReadAllText($file))
"""
    )
    head, _, launcher = output.partition("-----")
    assert "True|C:\\mock\\wt.exe" in head
    assert "-EncodedCommand" not in head
    assert ";" not in head.split("|")[-1]                      # wt.exe would split on it
    assert "ReadAllText" in head
    assert launcher.index("Tls12") < launcher.index("Invoke-RestMethod")
    assert "palindrome-rl/AGENT8088/AGENT8088-v1.2/install.ps1" in launcher
    assert "-Agent8088Home 'C:\\Users\\O''Brien\\Agent Home'" in launcher
    assert "-InstallDir 'C:\\Agent Install'" in launcher
    assert "-SkipSetup:$true" in launcher
    assert "Remove-Item -LiteralPath" in launcher              # the window removes the file itself
    assert ".started" in launcher                              # ...then reports that it started


def test_terminal_upgrade_runs_in_visible_external_bootstrap(tmp_path):
    output = _run_powershell(
        f"""
$env:TEMP = '{str(tmp_path).replace("'", "''")}'
$env:TMP = $env:TEMP
$env:SystemRoot = 'C:\\Windows'
$Branch = 'AGENT8088-v1.2'
$RepoSlug = 'palindrome-rl/AGENT8088'
$Agent8088Home = 'C:\\Users\\User\\AppData\\Local\\agent8088'
$InstallDir = ''
$InstallerSourceUrl = ''
$SkipSetup = $false
function Test-Path {{ param([string]$LiteralPath) return $true }}
{_handoff_stubs()}
{_powershell_function('ConvertTo-PowerShellLiteral')}
{_powershell_function('New-HandoffCommand')}
{_powershell_function('Get-InstallerInvocation')}
{_powershell_function('Start-TerminalUpgradeBootstrap')}
$result = Start-TerminalUpgradeBootstrap
$last = $script:startedArguments[-1]
$file = [regex]::Match($last, "ReadAllText\\('((?:[^']|'')*)'\\)").Groups[1].Value.Replace("''", "'")
$bootstrap = [IO.File]::ReadAllText($file)
Write-Output "$result|$script:startedFile"
Write-Output ($script:startedArguments -join '|')
Write-Output "-----"
Write-Output $bootstrap
"""
    )
    head, _, bootstrap = output.partition("-----")
    assert "True|C:\\Windows\\System32\\conhost.exe" in head
    assert "-EncodedCommand" not in head and "-EncodedCommand" not in bootstrap
    assert not re.search(r"[A-Za-z0-9+/=]{120,}", bootstrap)   # no base64 blob, nested or not
    assert "This window will remain open" in bootstrap
    assert "Agent8088 installation could not continue" in bootstrap
    assert "Read-Host" in bootstrap
    # The child gets the flag through its own handoff file, named in the helper.
    child_file = re.search(r"ReadAllText\(''(.*?)''\)", bootstrap).group(1)
    child = Path(child_file).read_text(encoding="utf-8")
    assert "-TerminalBootstrap" in child
    assert "Invoke-RestMethod" in child


@pytest.mark.parametrize(
    ("package_version", "answer", "expected", "bootstrap", "launch", "install"),
    [
        (None, "n", "failed", "False", "False", "False"),
        # Not installed and not hosting the installer: nothing can be closed, so
        # no helper window -- install here, then open the installer in it.
        (None, "y", "relaunched", "False", "True", "True"),
        (None, "c", "continue", "False", "False", "False"),
        # An old Windows Terminal may be the host: upgrading it could close this
        # window, so that case keeps the external helper.
        ("1.18.0.0", "y", "relaunched", "True", "False", "False"),
        ("1.18.0.0", "c", "continue", "False", "False", "False"),
        ("1.22.0.0", "unused", "relaunched", "False", "True", "False"),
    ],
)
def test_legacy_host_prompts_only_when_terminal_needs_upgrade(
    package_version, answer, expected, bootstrap, launch, install
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
$env:WT_SESSION = ''
$script:bootstrapCalled = $false
$script:launchCalled = $false
$script:installCalled = $false
function Test-TerminalCheckSkipped {{ return $false }}
function Test-SupportedTerminalHost {{ return $false }}
function Get-WindowsTerminalPackage {{ return {package} }}
function Write-Warn {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Read-Host {{ param([string]$Prompt) return '{answer}' }}
function Install-WindowsTerminal {{ param($ExistingPackage); $script:installCalled = $true; return $true }}
function Start-TerminalUpgradeBootstrap {{ $script:bootstrapCalled = $true; return $true }}
function Start-InstallerInWindowsTerminal {{ $script:launchCalled = $true; return $true }}
{_powershell_function('Ensure-SupportedTerminal')}
$result = Ensure-SupportedTerminal
Write-Output "$result|$script:bootstrapCalled|$script:launchCalled|$script:installCalled"
"""
    )
    assert output.splitlines()[-1] == f"{expected}|{bootstrap}|{launch}|{install}"


@pytest.mark.parametrize(
    ("switch", "env_value", "expected"),
    [
        ("$false", "", "False"),
        ("$true", "", "True"),
        ("$false", "1", "True"),
        ("$false", "true", "True"),
        ("$false", "YES", "True"),
        ("$false", "0", "False"),
        ("$false", "no", "False"),
    ],
)
def test_terminal_check_can_be_skipped(switch, env_value, expected):
    output = _run_powershell(
        f"""
$SkipTerminalCheck = {switch}
$env:AGENT8088_SKIP_TERMINAL_CHECK = '{env_value}'
{_powershell_function('Test-TerminalCheckSkipped')}
Write-Output (Test-TerminalCheckSkipped)
"""
    )
    assert output.splitlines()[-1] == expected


def test_skipping_the_terminal_check_never_prompts_or_relaunches():
    output = _run_powershell(
        f"""
$NonInteractive = $true
$TerminalBootstrap = $false
$script:touched = $false
function Test-TerminalCheckSkipped {{ return $true }}
function Test-SupportedTerminalHost {{ $script:touched = $true; return $false }}
function Get-WindowsTerminalPackage {{ $script:touched = $true; return $null }}
function Write-Warn {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Read-Host {{ $script:touched = $true; return 'y' }}
{_powershell_function('Ensure-SupportedTerminal')}
Write-Output "$(Ensure-SupportedTerminal)|$script:touched"
"""
    )
    assert output.splitlines()[-1] == "continue|False"


def test_unattended_install_without_a_terminal_says_how_to_continue():
    """An agent (Hermes, Claude Code) running the installer cannot answer the
    prompt; it used to fail with no way forward."""
    output = _run_powershell(
        f"""
$WindowsTerminalMinVersion = [version]'1.19.0.0'
$NonInteractive = $true
$TerminalBootstrap = $false
function Test-TerminalCheckSkipped {{ return $false }}
function Test-SupportedTerminalHost {{ return $false }}
function Get-WindowsTerminalPackage {{ return $null }}
function Write-Warn {{ param([string]$Message) }}
function Write-Info {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) Write-Output "ERR: $Message" }}
{_powershell_function('Ensure-SupportedTerminal')}
Write-Output (Ensure-SupportedTerminal)
"""
    )
    assert "failed" in output.splitlines()[-1]
    assert "AGENT8088_SKIP_TERMINAL_CHECK" in output


def test_new_enough_terminal_outside_windows_terminal_is_not_called_unsupported():
    """Seen live: 'requires 1.19, detected 1.24' under 'not supported'."""
    output = _run_powershell(
        f"""
$WindowsTerminalMinVersion = [version]'1.19.0.0'
$NonInteractive = $false
$TerminalBootstrap = $false
function Test-TerminalCheckSkipped {{ return $false }}
function Test-SupportedTerminalHost {{ return $false }}
function Get-WindowsTerminalPackage {{ return [pscustomobject]@{{ Version = '1.24.12741.0' }} }}
function Write-Warn {{ param([string]$Message) Write-Output "WARN: $Message" }}
function Write-Info {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Start-InstallerInWindowsTerminal {{ return $true }}
{_powershell_function('Ensure-SupportedTerminal')}
Write-Output (Ensure-SupportedTerminal)
"""
    )
    assert "relaunched" in output.splitlines()[-1]
    assert "not supported" not in output
    assert "not running inside Windows Terminal" in output


def test_terminal_bootstrap_installs_then_launches():
    output = _run_powershell(
        f"""
$WindowsTerminalMinVersion = [version]'1.19.0.0'
$NonInteractive = $false
$TerminalBootstrap = $true
$script:installCalled = $false
$script:launchCalled = $false
function Test-TerminalCheckSkipped {{ return $false }}
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
