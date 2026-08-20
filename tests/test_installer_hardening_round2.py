"""New install.ps1 pre-flight/resilience helpers, exercised in isolation.

Follows the extraction convention already used by test_installer_timeouts.py and
test_installer_python.py: regex one function out of install.ps1 and run it under
pwsh/powershell with its dependencies stubbed, rather than performing a real install.

NOT covered here, and not coverable this way:
  * Test-HostConnectivity / Test-SlowConnection - real network probes against
    github.com/astral.sh/raw.githubusercontent.com. A stub would only prove the stub
    works, not the probe.
  * Install-UvFromGitHubRelease / Install-WindowsTerminalFromGitHubRelease - both
    depend on real GitHub release layout/redirects.
  * The 32-bit Git branch's own long-standing behavior - already exercised
    informally; not part of this round.
These are best verified against a real Windows machine (or CI runner) with and
without network access, which this repo's test suite cannot do.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# [System.IO.Path]::GetPathRoot only parses "Z:\..." as a drive root on
# Windows; on macOS/Linux pwsh (.NET Core's Unix path rules) it returns "",
# which would make Test-DiskSpace vacuously pass regardless of what
# Get-PSDrive returns - a pass that proves nothing, so skip instead.
needs_windows_path_semantics = pytest.mark.skipif(
    sys.platform != "win32", reason="GetPathRoot only parses drive letters on Windows"
)


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
    # Write-Host (like the real Write-Warn/Write-Err), not Write-Output: the
    # latter would be captured into whatever the caller assigns a function's
    # return value to (e.g. `$r = Some-Function`), silently merging warning
    # text into $r instead of reaching the console - a real trap the actual
    # implementation avoids by using Write-Host for exactly this reason.
    stubs = (
        'function Write-Info { param([string]$Message) }\n'
        'function Write-Success { param([string]$Message) }\n'
        'function Write-Warn { param([string]$Message) Write-Host "WARN:$Message" }\n'
        'function Write-Err { param([string]$Message) Write-Host "ERR:$Message" }\n'
    )
    script = stubs + "\n".join(_powershell_function(f) for f in functions) + "\n" + body
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", script],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


# --------------------------------------------------------------------------
# Test-DiskSpace
# --------------------------------------------------------------------------
@needs_windows_path_semantics
def test_disk_space_below_threshold_fails():
    out = _run(
        '$Agent8088Home = "Z:\\agent8088"\n'
        'function Get-PSDrive { [CmdletBinding()] param([string]$Name) [pscustomobject]@{ Free = 1GB } }\n'
        '$r = Test-DiskSpace -MinimumGB 4\n'
        'Write-Output "Result=$r"',
        "Test-DiskSpace",
    )
    assert "Result=False" in out
    assert "ERR:Only 1 GB free" in out


@needs_windows_path_semantics
def test_disk_space_above_threshold_passes():
    out = _run(
        '$Agent8088Home = "Z:\\agent8088"\n'
        'function Get-PSDrive { [CmdletBinding()] param([string]$Name) [pscustomobject]@{ Free = 20GB } }\n'
        '$r = Test-DiskSpace -MinimumGB 4\n'
        'Write-Output "Result=$r"',
        "Test-DiskSpace",
    )
    assert "Result=True" in out


@needs_windows_path_semantics
def test_disk_space_check_that_itself_fails_does_not_block_install():
    """A drive type Get-PSDrive can't report on must not fail the whole install."""
    out = _run(
        '$Agent8088Home = "Z:\\agent8088"\n'
        'function Get-PSDrive { [CmdletBinding()] param([string]$Name) throw "no such drive" }\n'
        '$r = Test-DiskSpace -MinimumGB 4\n'
        'Write-Output "Result=$r"',
        "Test-DiskSpace",
    )
    assert "Result=True" in out


# --------------------------------------------------------------------------
# Test-LongPathsRegistryEnabled / Show-LongPathWarningIfNeeded
# --------------------------------------------------------------------------
def test_long_paths_enabled_reads_registry_value():
    out = _run(
        "function Get-ItemProperty { [pscustomobject]@{ LongPathsEnabled = 1 } }\n"
        "Write-Output (Test-LongPathsRegistryEnabled)",
        "Test-LongPathsRegistryEnabled",
    )
    assert out.strip() == "True"


def test_long_paths_disabled_when_registry_value_absent():
    out = _run(
        'function Get-ItemProperty { throw "not found" }\n'
        "Write-Output (Test-LongPathsRegistryEnabled)",
        "Test-LongPathsRegistryEnabled",
    )
    assert out.strip() == "False"


def test_long_path_warning_silent_but_sets_script_flag_when_disabled():
    out = _run(
        'function Get-ItemProperty { throw "not found" }\n'
        "Show-LongPathWarningIfNeeded\n"
        'Write-Output "Flag=$script:LongPathsEnabled"',
        "Test-LongPathsRegistryEnabled", "Show-LongPathWarningIfNeeded",
    )
    assert "WARN:" not in out
    assert "Flag=False" in out


def test_long_path_warning_silent_when_already_enabled():
    out = _run(
        "function Get-ItemProperty { [pscustomobject]@{ LongPathsEnabled = 1 } }\n"
        "Show-LongPathWarningIfNeeded\n"
        'Write-Output "Flag=$script:LongPathsEnabled"',
        "Test-LongPathsRegistryEnabled", "Show-LongPathWarningIfNeeded",
    )
    assert "WARN:" not in out
    assert "Flag=True" in out


# --------------------------------------------------------------------------
# Invoke-BoundedDownloadWithRetry
# --------------------------------------------------------------------------
def test_download_retry_succeeds_on_a_later_attempt():
    out = _run(
        '$Global:attempts = 0\n'
        'function Invoke-BoundedDownload {\n'
        '    param($Uri, $OutFile, $TimeoutSec, $Proxy)\n'
        '    $Global:attempts++\n'
        '    if ($Global:attempts -lt 2) { return @{ Success = $false; TimedOut = $false; Error = "503" } }\n'
        '    return @{ Success = $true; TimedOut = $false; Error = "" }\n'
        '}\n'
        '$r = Invoke-BoundedDownloadWithRetry -Uri "https://example.test/x" -OutFile "C:\\x" -TimeoutSec 5 -BackoffSec 0\n'
        'Write-Output "Success=$($r.Success) Attempts=$Global:attempts"',
        "Invoke-BoundedDownloadWithRetry",
    )
    assert "Success=True Attempts=2" in out
    assert "WARN:Download attempt 1/3 failed (503) - retrying..." in out


def test_download_retry_gives_up_after_max_attempts():
    out = _run(
        '$Global:attempts = 0\n'
        'function Invoke-BoundedDownload {\n'
        '    param($Uri, $OutFile, $TimeoutSec, $Proxy)\n'
        '    $Global:attempts++\n'
        '    return @{ Success = $false; TimedOut = $true; Error = "" }\n'
        '}\n'
        '$r = Invoke-BoundedDownloadWithRetry -Uri "https://example.test/x" -OutFile "C:\\x" -TimeoutSec 5 -BackoffSec 0 -MaxAttempts 3\n'
        'Write-Output "Success=$($r.Success) Attempts=$Global:attempts"',
        "Invoke-BoundedDownloadWithRetry",
    )
    assert "Success=False Attempts=3" in out


def test_download_retry_does_not_retry_on_first_success():
    out = _run(
        '$Global:attempts = 0\n'
        'function Invoke-BoundedDownload {\n'
        '    param($Uri, $OutFile, $TimeoutSec, $Proxy)\n'
        '    $Global:attempts++\n'
        '    return @{ Success = $true; TimedOut = $false; Error = "" }\n'
        '}\n'
        '$r = Invoke-BoundedDownloadWithRetry -Uri "https://example.test/x" -OutFile "C:\\x" -TimeoutSec 5 -BackoffSec 0\n'
        'Write-Output "Attempts=$Global:attempts"',
        "Invoke-BoundedDownloadWithRetry",
    )
    assert "Attempts=1" in out


# --------------------------------------------------------------------------
# Remove-IncompleteInstallDirectory: names the locking process
# --------------------------------------------------------------------------
def test_locked_directory_names_the_blocking_process():
    out = _run(
        '$InstallDir = "C:\\locked-agent8088"\n'
        'function Test-Path { param([string]$LiteralPath) return $true }\n'
        'function Remove-Item { throw "file is locked" }\n'
        'function Start-Sleep { param([int]$Seconds) }\n'
        'function Get-Process {\n'
        '    [pscustomobject]@{ ProcessName = "python"; Id = 4242; Path = "C:\\locked-agent8088\\venv\\Scripts\\python.exe" }\n'
        '}\n'
        'Write-Output (Remove-IncompleteInstallDirectory)',
        "Remove-IncompleteInstallDirectory",
    )
    assert out.splitlines()[-1] == "False"
    assert "ERR:  python (PID 4242) - C:\\locked-agent8088\\venv\\Scripts\\python.exe" in out
    assert "ERR:Close every Agent8088 session" not in out


def test_locked_directory_falls_back_to_generic_message_with_no_named_process():
    out = _run(
        '$InstallDir = "C:\\locked-agent8088"\n'
        'function Test-Path { param([string]$LiteralPath) return $true }\n'
        'function Remove-Item { throw "file is locked" }\n'
        'function Start-Sleep { param([int]$Seconds) }\n'
        'function Get-Process { }\n'
        'Write-Output (Remove-IncompleteInstallDirectory)',
        "Remove-IncompleteInstallDirectory",
    )
    assert out.splitlines()[-1] == "False"
    assert "ERR:Close every Agent8088 session, then run the installer again." in out


# --------------------------------------------------------------------------
# Resume/checkpoint markers
# --------------------------------------------------------------------------
def test_stage_marker_round_trips(tmp_path):
    out = _run(
        f'$Agent8088Home = "{tmp_path}"\n'
        'Write-Output "Before=$(Test-StageComplete -Stage "chromium")"\n'
        'Set-StageComplete -Stage "chromium"\n'
        'Write-Output "After=$(Test-StageComplete -Stage "chromium")"',
        "Get-StageMarkerPath", "Test-StageComplete", "Set-StageComplete",
    )
    assert "Before=False" in out
    assert "After=True" in out
    assert (tmp_path / ".install-stages" / "chromium.done").exists()


def test_stage_marker_is_independent_per_stage(tmp_path):
    out = _run(
        f'$Agent8088Home = "{tmp_path}"\n'
        'Set-StageComplete -Stage "gateway-extras"\n'
        'Write-Output "Gateway=$(Test-StageComplete -Stage "gateway-extras")"\n'
        'Write-Output "Search=$(Test-StageComplete -Stage "search-extras")"',
        "Get-StageMarkerPath", "Test-StageComplete", "Set-StageComplete",
    )
    assert "Gateway=True" in out
    assert "Search=False" in out


# --------------------------------------------------------------------------
# Install-Node-Bridge: 32-bit Windows no longer gets a broken x64 binary
# --------------------------------------------------------------------------
def test_32_bit_windows_skips_node_instead_of_downloading_x64_binary():
    out = _run(
        '$Agent8088Home = "C:\\agent8088"\n'
        '$InstallDir = "C:\\agent8088\\agent8088"\n'
        '$NodeVersion = "22.11.0"\n'
        'function Get-Command { [CmdletBinding()] param([string]$Name) $null }\n'
        'function Get-WindowsArch { "x86" }\n'
        'function Test-Path { [CmdletBinding()] param([string]$LiteralPath, [string]$Path) $false }\n'
        'function Register-SkippedStage { param([string]$Label, [string]$Reason, [string]$Fix) Write-Output "SKIPPED:${Label}:${Reason}" }\n'
        'function Invoke-BoundedDownloadWithRetry { throw "must not be called for 32-bit Windows" }\n'
        'Install-Node-Bridge\n'
        'Write-Output "NodeInstalled=$script:NodeInstalled"',
        "Install-Node-Bridge",
    )
    assert "SKIPPED:WhatsApp bridge (Node.js):32-bit Windows has no compatible Node.js build" in out
    assert "WARN:32-bit Windows detected" in out
    assert "NodeInstalled=" in out and "NodeInstalled=True" not in out


def test_64_bit_windows_still_resolves_x64_asset_name():
    """Guards the fix itself: only x86 is diverted, arm64/x64 keep their existing asset names."""
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    bridge = _powershell_function("Install-Node-Bridge")
    assert '$nodeArch = if ($arch -eq "arm64") { "arm64" } else { "x64" }' in bridge
    assert bridge.index('if ($arch -eq "x86")') < bridge.index('$nodeArch = if ($arch -eq "arm64")')


# --------------------------------------------------------------------------
# Install-Native-Sandbox: $SandboxInstalled must actually get set (pre-dated
# this PR - it was declared, read by Verify-Install, but never assigned, so
# a successful install still told the user native sandbox wasn't set up).
# --------------------------------------------------------------------------
def test_sandbox_success_sets_the_installed_flag():
    out = _run(
        '$InstallDir = "C:\\agent8088\\agent8088"\n'
        '$TSandboxSetup = 5\n'
        'function Test-Path { [CmdletBinding()] param([string]$LiteralPath) $true }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput) @{ ExitCode = 0 } }\n'
        'Install-Native-Sandbox\n'
        'Write-Output "SandboxInstalled=$script:SandboxInstalled"',
        "Install-Native-Sandbox",
    )
    assert "SandboxInstalled=True" in out


def test_sandbox_failure_leaves_the_installed_flag_false():
    out = _run(
        '$InstallDir = "C:\\agent8088\\agent8088"\n'
        '$TSandboxSetup = 5\n'
        'function Test-Path { [CmdletBinding()] param([string]$LiteralPath) $true }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput) @{ ExitCode = 1 } }\n'
        'function Write-StageWarning { param($Result, $TimeoutSec, $What, $Consequence, $Fix) }\n'
        'Install-Native-Sandbox\n'
        'Write-Output "SandboxInstalled=$script:SandboxInstalled"',
        "Install-Native-Sandbox",
    )
    assert "SandboxInstalled=" in out and "SandboxInstalled=True" not in out


# --------------------------------------------------------------------------
# A corrupted/unlaunchable managed binary must not crash the whole script -
# it should be treated the same as "not installed" and reinstalled.
# --------------------------------------------------------------------------
def test_uv_fast_path_falls_through_on_launch_exception(tmp_path):
    # First Test-Path call lies that a corrupted uv.exe already exists (so the
    # real `& $managedUv --version` below hits a genuinely nonexistent file and
    # throws a real launch exception); later calls report it's still missing,
    # so the function proceeds to its normal (stubbed) reinstall/fallback path
    # instead of crashing the whole script.
    out = _run(
        f'$Agent8088Home = "{tmp_path}"\n'
        '$TUvBoot = 1\n'
        '$Global:__calls = 0\n'
        'function Test-Path { [CmdletBinding()] param([string]$Path) $Global:__calls++; return ($Global:__calls -eq 1) }\n'
        'function Get-PowerShellHostExe { "pwsh" }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec) @{ TimedOut = $false; ExitCode = 1 } }\n'
        'function Install-UvFromGitHubRelease { $false }\n'
        'try { $r = Install-Uv } catch { Write-Output "THREW:$_"; $r = "threw" }\n'
        'Write-Output "Result=$r"',
        "Install-Uv",
    )
    assert "THREW:" not in out
    assert "WARN:Existing uv at" in out and "did not run - reinstalling" in out
    assert "Result=False" in out


def test_managed_node_fast_path_falls_through_on_launch_exception(tmp_path):
    out = _run(
        f'$Agent8088Home = "{tmp_path}"\n'
        f'$InstallDir = "{tmp_path / "agent8088"}"\n'
        '$TDownload = 1\n'
        '$Global:__calls = 0\n'
        'function Get-Command { [CmdletBinding()] param([string]$Name, $CommandType) $null }\n'
        'function Get-WindowsArch { "x64" }\n'
        # First Test-Path call (the managed-node check) lies that a corrupted
        # node.exe exists; every other Test-Path call reports "missing" so the
        # download step below is what actually runs next.
        'function Test-Path { [CmdletBinding()] param([string]$LiteralPath, [string]$Path) $Global:__calls++; return ($Global:__calls -eq 1) }\n'
        'function Invoke-BoundedDownloadWithRetry { @{ Success = $false; TimedOut = $false; Error = "network down" } }\n'
        'function Write-StageWarning { param($Result, $TimeoutSec, $What, $Consequence, $Fix) Write-Output "STAGEWARN:$What" }\n'
        'try { Install-Node-Bridge; Write-Output "Completed" } catch { Write-Output "THREW:$_" }',
        "Install-Node-Bridge",
    )
    assert "THREW:" not in out
    assert "WARN:Existing Node at" in out and "did not run - reinstalling" in out
    assert "Completed" in out


# --------------------------------------------------------------------------
# Failures that must now show up in the final Write-SkippedSummary instead
# of scrolling off-screen as a single Write-Warn line.
# --------------------------------------------------------------------------
def test_missing_venv_registers_a_skipped_stage_for_all_three_extras():
    out = _run(
        '$InstallDir = "C:\\agent8088\\agent8088"\n'
        'function Test-Path { [CmdletBinding()] param([string]$Path) $false }\n'
        'function Register-SkippedStage { param([string]$Label, [string]$Reason, [string]$Fix) Write-Output "SKIPPED:${Label}" }\n'
        'Install-Gateway-Extras',
        "Install-Gateway-Extras",
    )
    assert "SKIPPED:Gateway/search extras + Chromium" in out


def test_portable_node_install_failure_registers_a_skipped_stage():
    out = _run(
        '$Agent8088Home = "C:\\agent8088"\n'
        '$InstallDir = "C:\\agent8088\\agent8088"\n'
        'function Get-Command { [CmdletBinding()] param([string]$Name) $null }\n'
        'function Get-WindowsArch { "x64" }\n'
        'function Test-Path { [CmdletBinding()] param([string]$LiteralPath, [string]$Path) $false }\n'
        'function Invoke-BoundedDownloadWithRetry { throw "network down" }\n'
        'function Register-SkippedStage { param([string]$Label, [string]$Reason, [string]$Fix) Write-Output "SKIPPED:${Label}:${Reason}" }\n'
        'Install-Node-Bridge',
        "Install-Node-Bridge",
    )
    assert "SKIPPED:WhatsApp bridge (Node.js):portable Node install failed" in out


def test_missing_config_template_registers_a_skipped_stage(tmp_path):
    out = _run(
        f'$Agent8088Home = "{tmp_path}"\n'
        f'$InstallDir = "{tmp_path / "agent8088"}"\n'
        'function Test-Path { [CmdletBinding()] param([string]$Path) $false }\n'
        'function Register-SkippedStage { param([string]$Label, [string]$Reason, [string]$Fix) Write-Output "SKIPPED:${Label}:${Reason}" }\n'
        'Drop-Config',
        "Drop-Config",
    )
    assert "SKIPPED:Default config:no config.txt template found" in out


# --------------------------------------------------------------------------
# Set-StageComplete: a failed marker write must not fail silently, since a
# rerun would otherwise look like "resume" is broken with no explanation.
# --------------------------------------------------------------------------
def test_stage_marker_write_failure_warns_instead_of_failing_silently():
    out = _run(
        '$Agent8088Home = "Z:\\nonexistent\\definitely\\not\\writable"\n'
        'function New-Item { param($ItemType, $Path, [switch]$Force, $ErrorAction) throw "Access is denied" }\n'
        'Set-StageComplete -Stage "chromium"',
        "Get-StageMarkerPath", "Set-StageComplete",
    )
    assert "WARN:Could not save resume marker for 'chromium'" in out
