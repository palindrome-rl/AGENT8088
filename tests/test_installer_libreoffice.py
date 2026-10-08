"""Install-LibreOffice and its consent gate, exercised in isolation.

Follows the extraction convention used by test_installer_hardening_round2.py:
regex one function out of install.ps1 and run it under pwsh/powershell with
its dependencies stubbed, rather than performing a real install.

Two things worth actually proving. First, a failed or skipped LibreOffice
install must never abort the rest of setup — it registers a skipped stage and
returns, same contract as every other optional component in this installer
(Node.js, WhatsApp bridge). Second, the install is now opt-in: it is the
slowest stage in this installer by a wide margin, so nothing may download
~350 MB without an answer, and no path may block on a console read that
cannot be answered. Not covered here: a real WinGet install against the live
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
        '$env:CI = $null\n'
        '$NonInteractive = $false\n'
        'function Write-Info { param([string]$Message) }\n'
        'function Write-Success { param([string]$Message) Write-Host "SUCCESS:$Message" }\n'
        'function Write-Warn { param([string]$Message) Write-Host "WARN:$Message" }\n'
        'function Write-Err { param([string]$Message) Write-Host "ERR:$Message" }\n'
        'function Register-SkippedStage { param([string]$Label, [string]$Reason, [string]$Fix) '
        'Write-Host "SKIPPED:$Label|$Reason|$Fix" }\n'
        # Consent defaults to yes so the install-path tests below keep
        # exercising the install path. Tests that extract the real
        # Read-LibreOfficeConsent redefine it, and the later definition wins.
        'function Read-LibreOfficeConsent { $true }\n'
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
        'function Invoke-WithTimeout { @{ ExitCode = 0; TimedOut = $false; Output = "LibreOffice" } }\n'
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


# ---------------------------------------------------------------------------
# The consent gate
# ---------------------------------------------------------------------------


@needs_windows
def test_declining_never_invokes_winget_and_records_the_fix_line():
    """The whole point of the gate: 'no' must cost nothing but a response, and
    must still leave the winget command in the end-of-install summary."""
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "winget.exe" } }\n'
        'function Invoke-WithTimeout { throw "winget must not run after the user declines" }\n'
        'function Read-Host { param($Prompt) "no" }\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Read-LibreOfficeConsent", "Install-LibreOffice",
    )
    assert "Result=False" in out
    assert "SKIPPED:LibreOffice|not selected (optional, slow to install)" in out
    assert "winget install TheDocumentFoundation.LibreOffice" in out


@needs_windows
def test_accepting_at_the_prompt_proceeds_to_winget():
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) [pscustomobject]@{ Source = "winget.exe" } }\n'
        'function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput, $Activity) '
        'Write-Host "CALL:$FilePath"; @{ ExitCode = 1; TimedOut = $false; Output = "" } }\n'
        'function Read-Host { param($Prompt) "YES" }\n'
        '$TLibreOffice = 1800\n'
        '$null = Install-LibreOffice',
        "Read-LibreOfficeConsent", "Install-LibreOffice",
    )
    assert "CALL:winget.exe" in out


@needs_windows
def test_prompt_requires_an_explicit_yes_or_no():
    """Empty input, abbreviations, and arbitrary text must all re-prompt."""
    out = _run(
        '$script:answers = @("", "y", "maybe", "yes")\n'
        '$script:i = 0\n'
        'function Read-Host { param($Prompt) $a = $script:answers[$script:i]; $script:i++; '
        'Write-Host "ASKED"; $a }\n'
        '$r = Read-LibreOfficeConsent\n'
        'Write-Output "Result=$r"',
        "Read-LibreOfficeConsent",
    )
    assert out.count("ASKED") == 4
    assert "Result=True" in out


@needs_windows
def test_an_existing_install_is_never_prompted_about():
    """Nothing to consent to when soffice.exe is already on disk."""
    out = _run(
        'function Test-Path { param($Path) $Path -like "*LibreOffice\\program\\soffice.exe" -and $Path -notlike "*x86*" }\n'
        'function Read-LibreOfficeConsent { throw "must not ask about an install that already exists" }\n'
        'function Invoke-WithTimeout { @{ ExitCode = 0; TimedOut = $false; Output = "LibreOffice" } }\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Install-LibreOffice",
    )
    assert "Result=True" in out


@needs_windows
def test_missing_winget_is_never_prompted_about():
    """No point asking a question that cannot be acted on either way."""
    out = _run(
        'function Test-Path { param($Path) $false }\n'
        'function Get-Command { param($Name, $CommandType, $ErrorAction) $null }\n'
        'function Read-LibreOfficeConsent { throw "must not ask when WinGet cannot carry the answer out" }\n'
        '$r = Install-LibreOffice\n'
        'Write-Output "Result=$r"',
        "Install-LibreOffice",
    )
    assert "Result=False" in out
    assert "SKIPPED:LibreOffice|no WinGet available" in out


@needs_windows
@pytest.mark.parametrize(
    "value,expected",
    [("1", "True"), ("yes", "True"), ("TRUE", "True"),
     ("0", "False"), ("no", "False"), ("false", "False")],
)
def test_env_var_decides_without_reading_the_console(value, expected):
    """The documented install path is `iex (irm ...)`, which cannot pass a
    switch, so the env var is the only scriptable knob -- same convention as
    AGENT8088_BRANCH and AGENT8088_TIMEOUT_SCALE."""
    out = _run(
        '$env:AGENT8088_INSTALL_LIBREOFFICE = "' + value + '"\n'
        'function Read-Host { throw "the env var must decide without prompting" }\n'
        '$r = Read-LibreOfficeConsent\n'
        'Write-Output "Result=$r"',
        "Read-LibreOfficeConsent",
    )
    assert "Result=" + expected in out


@needs_windows
def test_an_unrecognised_env_value_falls_through_to_the_prompt():
    out = _run(
        '$env:AGENT8088_INSTALL_LIBREOFFICE = "sure"\n'
        'function Read-Host { param($Prompt) Write-Host "ASKED"; "no" }\n'
        '$r = Read-LibreOfficeConsent\n'
        'Write-Output "Result=$r"',
        "Read-LibreOfficeConsent",
    )
    assert "ASKED" in out
    assert "Result=False" in out


@needs_windows
@pytest.mark.parametrize(
    "switch,env_value,expected",
    [("WithLibreOffice", "no", "True"), ("SkipLibreOffice", "yes", "False")],
)
def test_an_explicit_switch_beats_the_env_var(switch, env_value, expected):
    out = _run(
        f'$env:AGENT8088_INSTALL_LIBREOFFICE = "{env_value}"\n'
        '$' + switch + ' = $true\n'
        'function Read-Host { throw "an explicit switch must not prompt" }\n'
        '$r = Read-LibreOfficeConsent\n'
        'Write-Output "Result=$r"',
        "Read-LibreOfficeConsent",
    )
    assert "Result=" + expected in out


@needs_windows
@pytest.mark.parametrize("setup", ['$NonInteractive = $true', '$env:CI = "true"'])
def test_a_host_that_cannot_answer_skips_instead_of_hanging(setup):
    """Read-Host returns empty immediately on a headless host, so the explicit
    answer loop would spin forever. The guard prevents that hang."""
    out = _run(
        setup + '\n'
        'function Read-Host { param($Prompt) "" }\n'
        '$r = Read-LibreOfficeConsent\n'
        'Write-Output "Result=$r"',
        "Read-LibreOfficeConsent",
    )
    assert "Result=False" in out


@needs_windows
def test_ci_false_does_not_silently_skip_the_prompt():
    out = _run(
        '$NonInteractive = $false\n'
        '$env:CI = "false"\n'
        'function Read-Host { param($Prompt) Write-Host "ASKED"; "no" }\n'
        '$r = Read-LibreOfficeConsent\n'
        'Write-Output "Result=$r"',
        "Read-LibreOfficeConsent",
    )
    assert "ASKED" in out
    assert "Result=False" in out


@needs_windows
def test_conflicting_switches_are_rejected():
    output = _run(
        '$WithLibreOffice = $true\n'
        '$SkipLibreOffice = $true\n'
        'try { $null = Read-LibreOfficeConsent; Write-Output "NO-ERROR" } '
        'catch { Write-Output $_.Exception.Message }',
        "Read-LibreOfficeConsent",
    )
    assert "cannot be used together" in output
    assert "NO-ERROR" not in output


@needs_windows
@pytest.mark.parametrize("switch", ["WithLibreOffice", "SkipLibreOffice"])
def test_selection_switch_survives_terminal_relaunch(switch):
    other = "SkipLibreOffice" if switch == "WithLibreOffice" else "WithLibreOffice"
    out = _run(
        '$Branch = "development"\n'
        '$Agent8088Home = "C:\\Agent Home"\n'
        '$InstallDir = "C:\\Agent Install"\n'
        '$SkipSetup = $false\n'
        '$TerminalBootstrap = $false\n'
        '$InstallerSourceUrl = "https://example.test/install.ps1"\n'
        '$RepoSlug = "palindrome-rl/AGENT8088"\n'
        f'${switch} = $true\n'
        f'${other} = $false\n'
        'Write-Output (Get-InstallerInvocation)',
        "ConvertTo-PowerShellLiteral", "Get-InstallerInvocation",
    )
    assert f"-{switch}" in out
    assert f"-{other}" not in out


def test_conflicting_switches_are_rejected_before_download_preflight():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    main = source.split("# Main\n# " + "-" * 76 + "\n", 1)[1]
    conflict_check = "if ($WithLibreOffice -and $SkipLibreOffice)"
    assert main.index(conflict_check) < main.index("Test-DiskSpace")
