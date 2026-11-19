"""Replay review-install failures without touching a real installation."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def ps_function(name):
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    return re.search(rf"(?ms)^function {name} \{{.*?^\}}", source).group()


def run_ps(tmp_path, script):
    host = shutil.which("powershell") or shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell not installed")
    path = tmp_path / "recovery with spaces.ps1"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("failure,ready,attempts", [
    ("success", True, 1), ("dns_then_success", True, 2),
    ("dns", False, 3), ("permission", False, 1),
    ("timeout", False, 1), ("missing_binary", False, 1),
    ("missing_pin", False, 0), ("missing_npm", False, 0),
    ("already_installed", True, 0),
])
def test_windows_recovery_is_bounded_logged_and_pinned(tmp_path, failure, ready, attempts):
    fake_npm = tmp_path / "npm.cmd"
    fake_npm.touch()
    pin = re.search(r'(?m)^\$script:OpenCodeReviewVersion = "[^"]+"',
                    (ROOT / "install.ps1").read_text(encoding="utf-8")).group()
    script = r'''
$ErrorActionPreference = 'Stop'
$script:calls = 0
$script:warnings = 0
$script:logged = @()
$TPip = 30
function Test-StageComplete { return ($failure -eq 'already_installed') }
function Set-StageComplete { }
function Write-Info { param($Message) }
function Write-Success { param($Message) }
function Write-Warn { param($Message) Write-Output $Message }
function Write-OutputTail { param($Text, $Lines) Write-Output $Text }
function Add-InstallLog { param($Title, $Text) $script:logged += $Text }
function Write-StageWarning { param($Result, $TimeoutSec, $What, $Consequence, $Fix)
    $script:warnings++; Write-Output $Fix }
function Start-Sleep { param($Seconds) }
function Find-VerifiedReviewExecutable { param($Prefix)
    if ($failure -eq 'already_installed' -or ($script:calls -gt 0 -and $failure -ne 'missing_binary')) { return 'verified.exe' }
    return '' }
function Invoke-WithTimeout { param($FilePath, $Arguments, $TimeoutSec, [switch]$CaptureOutput, $Activity)
    if (-not $CaptureOutput) { throw 'npm diagnostics were discarded' }
    if ($Arguments[-1] -ne '@alibaba-group/open-code-review@1.12.1') { throw 'unpinned install' }
    if ($Arguments -contains '--silent') { throw 'silent install' }
    $script:calls++
    $code = 0; $errorText = ''; $timedOut = $false
    if ($failure -eq 'dns' -or ($failure -eq 'dns_then_success' -and $script:calls -eq 1)) {
        $code = 1; $errorText = 'getaddrinfo ENOTFOUND github.com'
    } elseif ($failure -eq 'permission') { $code = 1; $errorText = 'EACCES permission denied'
    } elseif ($failure -eq 'timeout') { $code = -1; $timedOut = $true }
    return @{ ExitCode=$code; TimedOut=$timedOut; Output=''; ErrorOutput=$errorText }
}
'''
    # Exercise iex inside a child scope, as in the download-and-run installer.
    script += "\n& { Invoke-Expression '" + pin.replace("'", "''") + "' }\n"
    script += "$failure = '" + failure + "'\n"
    script += "$InstallDir = '" + str(tmp_path).replace("'", "''") + "'\n"
    script += "$script:NpmExe = '" + str(fake_npm).replace("'", "''") + "'\n"
    if failure == "missing_pin":
        script += "$script:OpenCodeReviewVersion=''\n"
    if failure == "missing_npm":
        script += "$script:NpmExe=''; function Get-Command { param($Name,$CommandType,$ErrorAction) return $null }\n"
    script += ps_function("Install-CodeReview") + "\nInstall-CodeReview\n"
    script += "@{ calls=$script:calls; ready=$script:ReviewInstalled; warnings=$script:warnings; logged=$script:logged } | ConvertTo-Json -Compress\n"
    output = run_ps(tmp_path, script)
    data = json.loads(output.strip().splitlines()[-1])
    assert data["calls"] == attempts
    assert data["ready"] is ready
    assert data["warnings"] == (0 if ready else 1)
    assert len(data["logged"]) == attempts
    if failure == "dns":
        assert "ENOTFOUND github.com" in output and "DNS lookup failed" in output
        assert "@alibaba-group/open-code-review@1.12.1" in output


@pytest.mark.parametrize("version,exit_code,valid", [
    ("open-code-review v1.12.1 (abc) windows/amd64", 0, True),
    ("open-code-review v1.12.12 (abc) windows/amd64", 0, False),
    ("open-code-review v1.12.1 (abc) windows/amd64", 1, False),
    ("", 0, False),
])
def test_windows_readiness_requires_running_the_exact_version(tmp_path, version, exit_code, valid):
    executable = tmp_path / "opencodereview.exe"
    executable.touch()
    script = "$script:OpenCodeReviewVersion='1.12.1'\n"
    script += "function Add-InstallLog { param($Title, $Text) }\n"
    script += "function Invoke-WithTimeout { param($FilePath,$Arguments,$TimeoutSec,[switch]$CaptureOutput)\n"
    script += f"return @{{ ExitCode={exit_code}; Output='{version}'; ErrorOutput='' }} }}\n"
    script += ps_function("Find-VerifiedReviewExecutable")
    script += "\n$result = Find-VerifiedReviewExecutable -Prefix '" + str(tmp_path).replace("'", "''") + "'\n"
    script += "Write-Output ('VALID=' + [bool]$result)\n"
    assert ("VALID=True" in run_ps(tmp_path, script)) is valid


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
@pytest.mark.parametrize("failure,ready,attempts", [
    ("success", True, 1), ("dns_then_success", True, 2),
    ("dns", False, 3), ("permission", False, 1), ("timeout", False, 1),
    ("missing_binary", False, 1),
])
def test_posix_recovery_is_bounded_logged_and_pinned(tmp_path, failure, ready, attempts):
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    function = re.search(r"(?ms)^install_code_review\(\) \{.*?^\}", source).group()
    script = r'''
set -eu
CALLS=0
OPEN_CODE_REVIEW_VERSION=1.12.1
REVIEW_EXECUTABLE=''
T_PIP=30
log_info() { :; }
log_warn() { echo "$*"; }
log_success() { :; }
record_skip() { echo "SKIP:$*"; }
warn_stage() { echo "SKIP:$*"; }
_write_stamp() { :; }
_stamp_matches() { return 1; }
sleep() { :; }
npm() { :; }
show_step_failure() { cat "$LAST_STEP_LOG"; }
find_verified_review_executable() {
    if [ "$CALLS" -gt 0 ] && [ "$FAILURE" != missing_binary ]; then echo verified; fi
}
run_logged() {
    CALLS=$((CALLS + 1))
    case "$*" in *'@alibaba-group/open-code-review@1.12.1') ;; *) return 99 ;; esac
    : > "$LAST_STEP_LOG"
    if [ "$FAILURE" = dns ] || { [ "$FAILURE" = dns_then_success ] && [ "$CALLS" -eq 1 ]; }; then
        echo 'getaddrinfo ENOTFOUND github.com' > "$LAST_STEP_LOG"; return 1
    fi
    if [ "$FAILURE" = permission ]; then echo 'EACCES' > "$LAST_STEP_LOG"; return 1; fi
    if [ "$FAILURE" = timeout ]; then return 124; fi
}
'''
    script += f"\nFAILURE='{failure}'\nINSTALL_DIR='{tmp_path}'\nLAST_STEP_LOG='{tmp_path}/step.log'\n"
    script += function + '\ninstall_code_review\necho "RESULT:$CALLS:$REVIEW_INSTALLED"\n'
    result = subprocess.run([shutil.which("bash"), "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"RESULT:{attempts}:{str(ready).lower()}" in result.stdout
    if failure == "dns":
        assert "DNS lookup failed" in result.stdout and "ENOTFOUND github.com" in result.stdout
