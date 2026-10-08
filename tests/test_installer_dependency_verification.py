"""Process-level verification for resumable Windows installer dependencies."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is Windows-only")


def _function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$", source)
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


def _shells():
    found = []
    for name in ("powershell.exe", "pwsh.exe"):
        path = shutil.which(name)
        if path and path not in found:
            found.append(path)
    return found


def _run(shell: str, body: str, *functions: str) -> str:
    script = "\n".join(_function(name) for name in functions) + "\n" + body
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", script], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@needs_windows
@pytest.mark.parametrize("shell", _shells())
def test_python_probe_runs_imports_in_the_target_interpreter(shell):
    python = sys.executable.replace("'", "''")
    out = _run(shell, f'''
$ok = Test-PythonModules -PythonExe '{python}' -Modules @('json', 'pathlib')
$missing = Test-PythonModules -PythonExe '{python}' -Modules @('agent8088_missing_probe_module')
Write-Output "OK=$ok MISSING=$missing"
''', "Invoke-WithTimeout", "Invoke-DependencyProbe", "Test-PythonModules")
    assert "OK=True MISSING=False" in out


@needs_windows
@pytest.mark.parametrize("shell", _shells())
def test_node_probe_loads_modules_from_the_requested_working_directory(shell, tmp_path):
    node = shutil.which("node.exe")
    if not node:
        pytest.skip("Node.js is unavailable")
    node = node.replace("'", "''")
    cwd = str(tmp_path).replace("'", "''")
    out = _run(shell, f'''
$ok = Test-NodeModules -NodeExe '{node}' -WorkingDirectory '{cwd}' -Modules @('fs', 'path')
$missing = Test-NodeModules -NodeExe '{node}' -WorkingDirectory '{cwd}' -Modules @('agent8088-missing-probe-module')
Write-Output "OK=$ok MISSING=$missing"
''', "Invoke-WithTimeout", "Invoke-DependencyProbe", "Test-NodeModules")
    assert "OK=True MISSING=False" in out


@needs_windows
@pytest.mark.parametrize("shell", _shells())
def test_web_dist_requires_the_referenced_javascript_bundle(shell, tmp_path):
    web = tmp_path / "web"
    assets = web / "dist" / "assets"
    assets.mkdir(parents=True)
    index = web / "dist" / "index.html"
    index.write_text('<script type="module" src="/assets/app.js"></script>', encoding="utf-8")
    escaped = str(web).replace("'", "''")
    out = _run(shell, f'''
Write-Output "Before=$(Test-WebUIDist -WebDir '{escaped}')"
Set-Content -LiteralPath '{str(assets / "app.js").replace("'", "''")}' -Value 'ok'
Write-Output "After=$(Test-WebUIDist -WebDir '{escaped}')"
''', "Test-WebUIDist")
    assert "Before=False" in out
    assert "After=True" in out


def test_every_resume_marker_has_a_live_health_probe():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    expectations = {
        "gateway-extras": "gatewayHealthy",
        "search-extras": "searchHealthy",
        "ocr": "ocrHealthy",
        "mem0": "mem0Healthy",
        "repository": "repositoryHealthy",
        "chromium": "chromiumHealthy",
        "codereview": "reviewHealthy",
    }
    for marker, health in expectations.items():
        assert re.search(
            rf'Test-StageComplete "{marker}"\) -and \${health}', source
        ), f"{marker} still trusts its marker without checking {health}"


def test_successful_installs_are_followed_by_real_verification():
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "core dependency verification failed" in source
    assert "git.exe did not run after extraction" in source
    assert "npm.cmd did not run after Node extraction" in source
    assert "ollama pull succeeded but the model is absent" in source
    assert "Repository checkout is incomplete; missing:" in source


@needs_windows
@pytest.mark.parametrize("shell", _shells())
def test_stale_gateway_marker_triggers_repair_and_reverification(shell, tmp_path):
    install = str(tmp_path).replace("'", "''")
    out = _run(shell, f'''
$InstallDir = '{install}'
$Agent8088Home = '{install}'
$script:UvCmd = 'uv.exe'
$TPip = 30
$TOcrModels = 30
$TChromium = 30
$script:GatewayExtrasInstalled = $false
$script:SearchExtrasInstalled = $false
$script:OcrInstalled = $false
$script:Mem0Installed = $false
$script:RepositoryInstalled = $false
$script:ChromiumInstalled = $false
$script:gatewayChecks = 0
function Test-Path {{ $true }}
function Test-StageComplete {{ $true }}
function Set-StageComplete {{ param($Stage) Write-Host "MARKED:$Stage" }}
function Test-PythonModules {{
    param($PythonExe, $Modules, $TimeoutSec)
    if ($Modules -contains 'slack_bolt') {{
        $script:gatewayChecks++
        return ($script:gatewayChecks -gt 1)
    }}
    return $true
}}
function Test-PythonCode {{ $true }}
function Read-Mem0Consent {{ $false }}
function Invoke-WithTimeout {{
    param($FilePath, $Arguments, $TimeoutSec, $Activity)
    Write-Host "INSTALL:$($Arguments -join '|')"
    return @{{ ExitCode = 0; TimedOut = $false }}
}}
function Write-Info {{ param($Message) }}
function Write-Success {{ param($Message) Write-Host "SUCCESS:$Message" }}
function Write-Warn {{ param($Message) Write-Host "WARN:$Message" }}
function Write-StageWarning {{ throw 'verification should recover' }}
Install-Gateway-Extras
Write-Host "READY:$script:GatewayExtrasInstalled CHECKS:$script:gatewayChecks"
''', "Install-Gateway-Extras")
    assert "Gateway resume marker is stale" in out
    assert "INSTALL:pip|install" in out
    assert "MARKED:gateway-extras" in out
    assert "READY:True CHECKS:2" in out
