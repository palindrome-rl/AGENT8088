"""The global install must ship a working `agent8088 --web`.

Two defects found while live-testing the Windows installer:

1. install.ps1 never builds the web frontend, so the installed copy has no
   ``web/dist`` and ``agent8088 --web`` dies in _build_frontend with a raw
   RuntimeError traceback. The fix: an Install-WebUI stage that runs npm
   install + npm run build, following the same extraction convention as
   test_installer_libreoffice.py (regex the function out of install.ps1 and
   run it under PowerShell with its dependencies stubbed).

2. web_server._frontend_root() only looks under the source checkout (CWD
   walk). From the installed location — e.g. C:\\Windows\\System32 — there is
   no web/ above the package, so --web can never find the frontend even when
   one exists. The fix: also look next to the installed package itself,
   which is where the installer clones the repo (InstallDir\\src\\agent8088
   is a source checkout, so parents[2] resolves there).
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
        '$NonInteractive = $false\n'
        '$WebUIReady = $false\n'
        'function Write-Info { param([string]$Message) Write-Host "INFO:$Message" }\n'
        'function Write-Success { param([string]$Message) Write-Host "SUCCESS:$Message" }\n'
        'function Write-Warn { param([string]$Message) Write-Host "WARN:$Message" }\n'
        'function Write-Err { param([string]$Message) Write-Host "ERR:$Message" }\n'
        'function Register-SkippedStage { param([string]$Label, [string]$Reason, [string]$Fix) '
        'Write-Host "SKIPPED:$Label|$Reason|$Fix" }\n'
        'function Write-StageWarning { param($Result, [int]$TimeoutSec, [string]$What, [string]$Consequence, [string]$Fix) '
        'Write-Host "STAGEWARN:$What|$Consequence|$Fix" }\n'
        'function Invoke-WithTimeout { param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec, [string]$Activity) '
        'Write-Host "RUN:$FilePath $($Arguments -join [char]32)"; return @{ ExitCode = 0 } }\n'
    )
    script = stubs + "\n".join(_powershell_function(f) for f in functions) + "\n" + body
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@needs_windows
class TestInstallWebUIStage:
    """Install-WebUI: build web/dist during install; skip, never abort."""

    def test_installs_and_builds_when_npm_available(self, tmp_path):
        # package.json exists, npm stub succeeds → build runs, dist flag set.
        # $script:NpmExe must point at a real file: Install-WebUI gates on
        # Test-Path, exactly like the real installer's node dir.
        npm_exe = tmp_path / "npm.cmd"
        npm_exe.write_text("@echo off", encoding="utf-8")
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        body = f"""
$InstallDir = "{tmp_path}"
$script:NpmExe = "{npm_exe}"
$script:NodeInstalled = $true
function Invoke-WithTimeout {{ param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec, [string]$Activity)
    Write-Host "RUN:$FilePath $($Arguments -join [char]32)"
    if ($Arguments -contains "build") {{ New-Item -ItemType Directory -Force -Path "{web_dir}\\dist" | Out-Null; New-Item -ItemType File -Force -Path "{web_dir}\\dist\\index.html" | Out-Null }}
    return @{{ ExitCode = 0 }} }}
Install-WebUI
Write-Host "FLAG:WebUIReady=$script:WebUIReady"
"""
        out = _run(body, "Install-WebUI")
        assert f"RUN:{npm_exe} install" in out
        assert f"RUN:{npm_exe} run build" in out
        assert "FLAG:WebUIReady=True" in out
        assert "SKIPPED:" not in out

    def test_existing_dist_is_not_rebuilt(self, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        (web_dir / "dist").mkdir()
        (web_dir / "dist" / "index.html").write_text("x", encoding="utf-8")
        body = f"""
$InstallDir = "{tmp_path}"
$script:NpmExe = "C:\\fake\\npm.cmd"
$script:NodeInstalled = $true
Install-WebUI
Write-Host "FLAG:WebUIReady=$script:WebUIReady"
"""
        out = _run(body, "Install-WebUI")
        assert "already built" in out
        assert "RUN:" not in out
        assert "FLAG:WebUIReady=True" in out

    def test_missing_package_json_skips_cleanly(self, tmp_path):
        # no web/ at all → skipped stage, flag False
        body = f"""
$InstallDir = "{tmp_path}"
$script:NpmExe = "C:\\fake\\npm.cmd"
$script:NodeInstalled = $true
Install-WebUI
Write-Host "FLAG:WebUIReady=$script:WebUIReady"
"""
        out = _run(body, "Install-WebUI")
        assert "SKIPPED:Web UI" in out
        assert "FLAG:WebUIReady=False" in out

    def test_no_npm_registers_skipped_stage(self, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        body = f"""
$InstallDir = "{tmp_path}"
$script:NpmExe = $null
$script:NodeInstalled = $false
Install-WebUI
Write-Host "FLAG:WebUIReady=$script:WebUIReady"
"""
        out = _run(body, "Install-WebUI")
        assert "SKIPPED:Web UI" in out
        assert "nodejs.org" in out
        assert "FLAG:WebUIReady=False" in out

    def test_build_failure_registers_skipped_stage_not_throw(self, tmp_path):
        npm_exe = tmp_path / "npm.cmd"
        npm_exe.write_text("@echo off", encoding="utf-8")
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        body = f"""
$InstallDir = "{tmp_path}"
$script:NpmExe = "{npm_exe}"
$script:NodeInstalled = $true
function Invoke-WithTimeout {{ param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec, [string]$Activity)
    Write-Host "RUN:$FilePath $($Arguments -join [char]32)"
    if ($Arguments -contains "install") {{ return @{{ ExitCode = 0 }} }}
    return @{{ ExitCode = 1; Stderr = "build error" }} }}
Install-WebUI
Write-Host "FLAG:WebUIReady=$script:WebUIReady"
"""
        out = _run(body, "Install-WebUI")
        assert "STAGEWARN:Web UI build" in out
        assert "npm run build" in out
        assert "FLAG:WebUIReady=False" in out


class TestFrontendRootResolution:
    """_frontend_root must find the frontend next to the installed package."""

    def test_finds_web_dir_next_to_installed_package(self, monkeypatch, tmp_path):
        from agent8088 import web_server

        installed_root = tmp_path / "agent8088" / "src" / "agent8088"
        installed_root.mkdir(parents=True)
        web_dir = tmp_path / "agent8088" / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(web_server, "__file__", str(installed_root / "web_server.py"))
        # CWD walk finds nothing (as when launched from C:\Windows\System32)
        empty_cwd = tmp_path / "nowhere"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)

        found = web_server._frontend_root()
        assert found is not None
        assert found == web_dir

    def test_prefers_cwd_checkout_over_installed(self, monkeypatch, tmp_path):
        from agent8088 import web_server

        checkout_web = tmp_path / "checkout" / "web"
        checkout_web.mkdir(parents=True)
        (checkout_web / "package.json").write_text("{}", encoding="utf-8")
        installed_root = tmp_path / "agent8088" / "src" / "agent8088"
        installed_root.mkdir(parents=True)
        installed_web = tmp_path / "agent8088" / "web"
        installed_web.mkdir()
        (installed_web / "package.json").write_text("{}", encoding="utf-8")

        monkeypatch.chdir(tmp_path / "checkout")
        monkeypatch.setattr(web_server, "__file__", str(installed_root / "web_server.py"))

        found = web_server._frontend_root()
        assert found == checkout_web


class TestWebLaunchFailureMessage:
    """`agent8088 --web` without a frontend must print guidance, not a traceback."""

    def test_runtime_error_message_is_actionable(self, monkeypatch, tmp_path):
        from agent8088 import web_server

        monkeypatch.setattr(web_server, "_frontend_root", lambda: None)
        with pytest.raises(RuntimeError) as excinfo:
            web_server.run_web_server(dev=False)
        message = str(excinfo.value)
        assert "web UI frontend is missing" in message
        assert "npm install" in message
        assert "npm run build" in message