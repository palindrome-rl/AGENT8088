"""The Windows Terminal handoff must not report success it has not seen.

Reported on Windows 10 with no Windows Terminal: the user answered `y`, the
"Agent8088 Terminal Setup" window printed the banner and then "[OK] Windows
Terminal is ready. Agent8088 installation is continuing in the new window."
-- and no window ever opened. Two causes:

1. Main reset the exit status with Set-InstallerExitStatus right after the
   banner. In the -TerminalBootstrap child that function calls `exit`, so the
   child quit with status 0 before installing anything, and the helper took
   status 0 as success.
2. Start-InstallerInWindowsTerminal reported success as soon as Start-Process
   returned, which on a fresh install does not mean a window opened.

These run under pwsh on any platform: only the Windows-only commands are stubbed.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="PowerShell is not installed")


def _source() -> str:
    return (ROOT / "install.ps1").read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$", _source())
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


def _main() -> str:
    return _source().split("# Main\n# " + "-" * 76 + "\n", 1)[1]


def _run(script: str, tmp_path: Path, **env) -> subprocess.CompletedProcess:
    script_file = tmp_path / "run.ps1"
    script_file.write_text(script, encoding="utf-8")
    environment = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path),
                   "TMP": str(tmp_path), **env}
    # Match the installer's plain-text handoff on hosts that disallow -File.
    literal = "'" + str(script_file).replace("'", "''") + "'"
    command = f"Invoke-Expression ([IO.File]::ReadAllText({literal}))"
    return subprocess.run([POWERSHELL, "-NoProfile", "-Command", command],
                          capture_output=True, text=True, env=environment, timeout=60)


_OUTPUT_STUBS = """
# Write-Host, like the real helpers: Write-Output would leak into return values.
function Write-Banner  { Write-Host 'BANNER' }
function Write-Info    { param([string]$Message) Write-Host "INFO $Message" }
function Write-Success { param([string]$Message) Write-Host "OK $Message" }
function Write-Warn    { param([string]$Message) Write-Host "WARN $Message" }
function Write-Err     { param([string]$Message) Write-Host "ERR $Message" }
"""


def test_the_bootstrap_child_gets_past_the_banner(tmp_path):
    # Main inline, not dot-sourced: `exit` in a dot-sourced file does not set
    # the process exit code, and that code is what the helper window reads.
    result = _run(f"""
$TerminalBootstrap = $true
$WithLibreOffice = $false; $SkipLibreOffice = $false; $WithMem0 = $false; $SkipMem0 = $false
{_function('Set-InstallerExitStatus')}
{_OUTPUT_STUBS}
function Test-DiskSpace {{ return $true }}
function Show-LongPathWarningIfNeeded {{ }}
function Show-AdministratorWarningIfNeeded {{ }}
function Start-InstallLog {{ }}
function Write-SkippedSummary {{ }}
function Test-HostConnectivity {{ }}
function Test-SlowConnection {{ return $false }}
function Wait-ForPendingUninstall {{ return $true }}
function Ensure-SupportedTerminal {{ Write-Host 'REACHED-TERMINAL-GATE'; return 'failed' }}
""" + _main(), tmp_path)
    assert "BANNER" in result.stdout
    # Before the fix the child exited 0 right here, before the gate that installs
    # Windows Terminal -- and the helper window called that success.
    assert "REACHED-TERMINAL-GATE" in result.stdout
    assert result.returncode == 1, result.stdout + result.stderr  # the gate's failure reaches the helper


def _handoff_script(start_process: str) -> str:
    return f"""
$Branch = 'development'; $Agent8088Home = 'C:\\a8088'; $InstallDir = ''
{_OUTPUT_STUBS}
function Get-WindowsTerminalPackage {{ return $null }}
function Get-WindowsTerminalExecutable {{ param($Package) return 'C:\\wt.exe' }}
function Get-PowerShellHostExe {{ return 'powershell.exe' }}
function Get-InstallerInvocation {{ param([switch]$PreferLocalScript) return "Write-Output 'installer-ran'" }}
{start_process}
{_function('ConvertTo-PowerShellLiteral')}
{_function('New-HandoffCommand')}
{_function('New-HandoffMarker')}
{_function('Get-HandoffTimeoutSeconds')}
{_function('Wait-HandoffStarted')}
{_function('Write-HandoffFallbackHelp')}
{_function('Start-InstallerInWindowsTerminal')}
$started = Get-Date
$result = Start-InstallerInWindowsTerminal
Write-Output "RESULT $result $([int]((Get-Date) - $started).TotalSeconds)"
Write-Output "LEFTOVER $(@(Get-ChildItem -LiteralPath (Join-Path ([IO.Path]::GetTempPath()) 'agent8088-handoff') -Filter '*.started' -ErrorAction SilentlyContinue).Count)"
"""


def test_success_is_reported_only_once_the_new_window_has_started(tmp_path):
    # Stand-in for wt.exe: run the handoff text the way the new window would.
    start_process = r"""
function Start-Process {
    param([string]$FilePath, [object[]]$ArgumentList)
    $handoff = ([string]$ArgumentList[-1]).Trim('"')
    $script:windowOutput = Invoke-Expression $handoff
}
"""
    result = _run(_handoff_script(start_process), tmp_path)
    out = result.stdout
    assert "RESULT True" in out, out + result.stderr
    assert "OK Installation is continuing in the new Windows Terminal window." in out
    assert "LEFTOVER 0" in out  # marker consumed


def test_a_window_that_never_starts_is_reported_with_a_way_forward(tmp_path):
    # wt.exe "launched" but nothing ran: what a fresh Windows 10 install can do.
    start_process = """
function Start-Process { param([string]$FilePath, [object[]]$ArgumentList) }
"""
    began = time.monotonic()
    result = _run(_handoff_script(start_process), tmp_path, AGENT8088_HANDOFF_TIMEOUT="2")
    out = result.stdout
    assert time.monotonic() - began < 30
    assert "RESULT False" in out, out + result.stderr
    assert "ERR Windows Terminal did not start the installer within 2 seconds." in out
    assert "AGENT8088_SKIP_TERMINAL_CHECK" in out
    assert "Open Windows Terminal yourself" in out
    assert "OK Installation is continuing" not in out
    assert "LEFTOVER 0" in out


def test_a_launch_that_throws_is_reported_with_a_way_forward(tmp_path):
    start_process = """
function Start-Process { param([string]$FilePath, [object[]]$ArgumentList) throw 'Access is denied' }
"""
    result = _run(_handoff_script(start_process), tmp_path)
    out = result.stdout
    assert "RESULT False" in out
    assert "ERR Could not launch Windows Terminal: Access is denied" in out
    assert "Open Windows Terminal yourself" in out
    assert "LEFTOVER 0" in out


def test_the_bootstrap_child_fails_when_the_new_window_never_starts(tmp_path):
    # End to end through the gate in -TerminalBootstrap mode: a silent launch
    # must come back as "failed" (child exits 1, helper shows the red error and
    # waits) instead of "relaunched" (helper prints "[OK] ... continuing").
    result = _run(f"""
$WindowsTerminalMinVersion = [version]'1.19.0.0'
$NonInteractive = $false
$TerminalBootstrap = $true
$Branch = 'development'; $Agent8088Home = 'C:\\a8088'; $InstallDir = ''
{_OUTPUT_STUBS}
function Test-TerminalCheckSkipped {{ return $false }}
function Test-SupportedTerminalHost {{ return $false }}
function Get-WindowsTerminalPackage {{ return $null }}
function Get-WindowsTerminalExecutable {{ param($Package) return 'C:\\wt.exe' }}
function Get-PowerShellHostExe {{ return 'powershell.exe' }}
function Get-InstallerInvocation {{ param([switch]$PreferLocalScript) return "Write-Output 'installer-ran'" }}
function Install-WindowsTerminal {{ param($ExistingPackage) return $true }}
function Start-Process {{ param([string]$FilePath, [object[]]$ArgumentList) }}
{_function('ConvertTo-PowerShellLiteral')}
{_function('New-HandoffCommand')}
{_function('New-HandoffMarker')}
{_function('Get-HandoffTimeoutSeconds')}
{_function('Wait-HandoffStarted')}
{_function('Write-HandoffFallbackHelp')}
{_function('Start-InstallerInWindowsTerminal')}
{_function('Ensure-SupportedTerminal')}
Write-Output "GATE $(Ensure-SupportedTerminal)"
""", tmp_path, AGENT8088_HANDOFF_TIMEOUT="1")
    assert "GATE failed" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize(("value", "expected"),
                         [("", "60"), ("abc", "60"), ("0", "60"), ("-5", "60"), ("90", "90")])
def test_handoff_timeout_setting(tmp_path, value, expected):
    result = _run(f"""
{_function('Get-HandoffTimeoutSeconds')}
Write-Output (Get-HandoffTimeoutSeconds)
""", tmp_path, AGENT8088_HANDOFF_TIMEOUT=value)
    assert result.stdout.strip() == expected


def test_install_ps1_stays_ascii_and_crlf():
    raw = (ROOT / "install.ps1").read_bytes()
    raw.decode("ascii")
    lines = raw.split(b"\n")[:-1]
    assert lines and all(line.endswith(b"\r") for line in lines)
