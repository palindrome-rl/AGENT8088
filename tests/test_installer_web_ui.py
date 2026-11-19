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
        'function Invoke-WithTimeout { param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec, [string]$Activity, [string]$WorkingDirectory, [switch]$CaptureOutput) '
        'Write-Host "RUN:$FilePath $($Arguments -join [char]32)"; return @{ ExitCode = 0 } }\n'
    )
    expanded = []
    for function in functions:
        if function == "Install-WebUI":
            # Get/Set-WebSourceStamp are the source-hash gate that stops a
            # dist/ built from older web/ sources being served as current.
            expanded.extend(("Get-WebSourceStamp", "Set-WebSourceStamp",
                             "Test-WebUIDist"))
        if function == "Install-Node-Bridge":
            expanded.extend(("Invoke-DependencyProbe", "Test-NodeModules"))
        expanded.append(function)
    # Preserve order while avoiding duplicate function definitions.
    expanded = list(dict.fromkeys(expanded))
    script = stubs + "\n".join(_powershell_function(f) for f in expanded) + "\n" + body
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(params=["powershell", "pwsh"])
def windows_shell(request, monkeypatch):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is not installed")
    monkeypatch.setattr(sys.modules[__name__], "_powershell", lambda: executable)


@needs_windows
@pytest.mark.usefixtures("windows_shell")
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
function Invoke-WithTimeout {{ param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec, [string]$Activity, [string]$WorkingDirectory, [switch]$CaptureOutput)
    Write-Host "RUN:$FilePath $($Arguments -join [char]32)"
    if ($Arguments -contains "build") {{
        New-Item -ItemType Directory -Force -Path "{web_dir}\\dist\\assets" | Out-Null
        Set-Content -Path "{web_dir}\\dist\\index.html" -Value '<script type="module" src="/assets/app.js"></script>'
        Set-Content -Path "{web_dir}\\dist\\assets\\app.js" -Value 'console.log("ok")'
    }}
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
        # No git repo here, so Get-WebSourceStamp returns "" and the stamp
        # gate is skipped: an unverifiable-but-present dist is still not
        # rebuilt, which is install.sh's rule too.
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        (web_dir / "dist").mkdir()
        (web_dir / "dist" / "assets").mkdir()
        (web_dir / "dist" / "index.html").write_text(
            '<script type="module" src="/assets/app.js"></script>', encoding="utf-8"
        )
        (web_dir / "dist" / "assets" / "app.js").write_text("ok", encoding="utf-8")
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

    def test_a_dist_built_from_other_sources_is_rebuilt(self, tmp_path):
        """The S42 regression: a stamp naming a different web/ tree is stale.

        Clone-Repo fetched new sources; the old bundle used to be served
        anyway because Test-WebUIDist only checked that dist existed.
        """
        npm_exe = tmp_path / "npm.cmd"
        npm_exe.write_text("@echo off", encoding="utf-8")
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        (web_dir / "dist").mkdir()
        (web_dir / "dist" / "assets").mkdir()
        (web_dir / "dist" / "index.html").write_text(
            '<script type="module" src="/assets/app.js"></script>', encoding="utf-8"
        )
        (web_dir / "dist" / "assets" / "app.js").write_text("old bundle", encoding="utf-8")
        (web_dir / "dist" / ".agent8088-build-stamp").write_text(
            "0000000000000000000000000000000000000000", encoding="utf-8")
        body = f"""
$InstallDir = "{tmp_path}"
$script:NpmExe = "{npm_exe}"
$script:NodeInstalled = $true
function Invoke-WithTimeout {{ param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec, [string]$Activity, [string]$WorkingDirectory, [switch]$CaptureOutput)
    Write-Host "RUN:$FilePath $($Arguments -join [char]32)"
    if ($Arguments -contains "build") {{
        Set-Content -Path "{web_dir}\\dist\\index.html" -Value '<script type="module" src="/assets/app.js"></script>'
        Set-Content -Path "{web_dir}\\dist\\assets\\app.js" -Value 'console.log("fresh")'
    }}
    return @{{ ExitCode = 0 }} }}
function Get-WebSourceStamp {{ return "1111111111111111111111111111111111111111" }}
Install-WebUI
Write-Host "FLAG:WebUIReady=$script:WebUIReady"
"""
        out = _run(body, "Install-WebUI")
        assert "already built" not in out
        assert "run build" in out
        assert "FLAG:WebUIReady=True" in out
        # The build recorded the tree it now corresponds to.
        assert (web_dir / "dist" / ".agent8088-build-stamp").read_text(
            encoding="utf-8").strip() == "1111111111111111111111111111111111111111"

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
function Invoke-WithTimeout {{ param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec, [string]$Activity, [string]$WorkingDirectory, [switch]$CaptureOutput)
    Write-Host "RUN:$FilePath $($Arguments -join [char]32)"
    if ($Arguments -contains "install") {{ return @{{ ExitCode = 0 }} }}
    return @{{ ExitCode = 1; Stderr = "build error" }} }}
Install-WebUI
Write-Host "FLAG:WebUIReady=$script:WebUIReady"
"""
        out = _run(body, "Install-WebUI")
        assert "STAGEWARN:Web UI build" in out
        assert "npm.cmd run build" in out
        assert "FLAG:WebUIReady=False" in out

    def test_partial_dependencies_reinstalled_from_lockfile(self, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        lock = web_dir / "package-lock.json"
        lock.write_text('{"sentinel":true}', encoding="utf-8")
        (web_dir / "node_modules").mkdir()
        npm_exe = tmp_path / "npm.cmd"
        npm_exe.write_text("@echo off", encoding="utf-8")
        out = _run(f'''
$InstallDir = "{tmp_path}"
$script:NpmExe = "{npm_exe}"
$env:NODE_ENV = "production"
$env:npm_config_omit = "optional"
function Invoke-WithTimeout {{
    param($FilePath, $Arguments, $TimeoutSec, $Activity, $WorkingDirectory, [switch]$CaptureOutput)
    Write-Host "ARGS:$($Arguments -join '|')"
    Write-Host "CWD:$WorkingDirectory CAPTURE:$CaptureOutput"
    if ($Arguments -contains "build") {{
        New-Item -ItemType Directory -Force "{web_dir}\\dist\\assets" | Out-Null
        Set-Content -Path "{web_dir}\\dist\\index.html" -Value '<script type="module" src="/assets/app.js"></script>'
        Set-Content -Path "{web_dir}\\dist\\assets\\app.js" -Value 'console.log("ok")'
    }}
    return @{{ ExitCode = 0 }}
}}
Install-WebUI
Write-Host "READY:$script:WebUIReady"
''', "Install-WebUI")
        assert f"ARGS:ci|--prefix|{web_dir}|--include=dev|--include=optional" in out
        assert f"CWD:{web_dir} CAPTURE:True" in out
        assert "READY:True" in out
        assert lock.read_text(encoding="utf-8") == '{"sentinel":true}'

    def test_dependency_failure_stops_before_build_and_reports_details(self, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "package.json").write_text("{}", encoding="utf-8")
        (web_dir / "package-lock.json").write_text("{}", encoding="utf-8")
        npm_exe = tmp_path / "npm.cmd"
        npm_exe.write_text("@echo off", encoding="utf-8")
        out = _run(f'''
$InstallDir = "{tmp_path}"
$script:NpmExe = "{npm_exe}"
$TNpm = 300
function Invoke-WithTimeout {{
    param($FilePath, $Arguments, $TimeoutSec, $Activity, $WorkingDirectory, [switch]$CaptureOutput)
    Write-Host "ARGS:$($Arguments -join '|')"
    return @{{ ExitCode = 1; ErrorOutput = "npm ERR! ECONNRESET" }}
}}
Install-WebUI
Write-Host "READY:$script:WebUIReady"
''', "Write-StageWarning", "Install-WebUI")
        assert "ECONNRESET" in out
        assert "ARGS:run|build" not in out
        assert "npm.cmd ci --include=dev --include=optional" in out
        assert "READY:False" in out

    def test_diagnostics_keep_cause_and_bound_stack_trace(self):
        out = _run('''
$stack = @("Cannot find native binding") + (1..20 | ForEach-Object { "frame $_" })
$stack += "https://user:secret@example.test/package"
Write-StageWarning -Result @{ ExitCode = 1; ErrorOutput = ($stack -join "`n") } `
    -TimeoutSec 300 -What "Web UI build" -Consequence "unavailable" -Fix "rerun"
''', "Write-StageWarning")
        assert "Cannot find native binding" in out
        assert "frame 10" not in out
        assert "frame 20" in out
        assert "https://***@example.test/package" in out
        assert "secret" not in out


@needs_windows
@pytest.mark.usefixtures("windows_shell")
class TestInstallNodeBridgeDependencies:
    @pytest.mark.parametrize("exit_code", [0, 1])
    def test_partial_bridge_is_reinstalled_before_marked_ready(self, tmp_path, exit_code):
        node = shutil.which("node.exe")
        if not node or not Path(node).with_name("npm.cmd").exists():
            pytest.skip("Node with a matching npm.cmd is required")
        bridge = tmp_path / "src/agent8088/gateway/platforms/whatsapp_bridge"
        bridge.mkdir(parents=True)
        (bridge / "package.json").write_text("{}", encoding="utf-8")
        (bridge / "package-lock.json").write_text("{}", encoding="utf-8")
        (bridge / "node_modules").mkdir()
        out = _run(f'''
$InstallDir = "{tmp_path}"
$Agent8088Home = "{tmp_path}"
$script:LongPathsEnabled = $true
$script:WhatsAppBridgeReady = $false
$TNpm = 300
function Get-Command {{ param($Name, $CommandType, $ErrorAction) [pscustomobject]@{{ Source = "{node}" }} }}
function Invoke-WithTimeout {{
    param($FilePath, $Arguments, $TimeoutSec, $Activity, $WorkingDirectory, [switch]$CaptureOutput)
    Write-Host "ARGS:$($Arguments -join '|')"
    Write-Host "CWD:$WorkingDirectory CAPTURE:$CaptureOutput"
    $code = if ($Arguments.Count -eq 1 -and $Arguments[0] -eq '--version') {{ 0 }} else {{ {exit_code} }}
    return @{{ ExitCode = $code }}
}}
Install-Node-Bridge
Write-Host "READY:$script:WhatsAppBridgeReady"
''', "Install-Node-Bridge")
        assert f"ARGS:ci|--prefix|{bridge}|--include=optional" in out
        assert f"CWD:{bridge} CAPTURE:True" in out
        assert f"READY:{'True' if exit_code == 0 else 'False'}" in out


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
