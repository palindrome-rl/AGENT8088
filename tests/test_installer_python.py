import os
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


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        pytest.skip("PowerShell is not installed")
    return executable


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows installer with cmd shims")
def test_fresh_uv_python_install_records_the_executable(tmp_path):
    fake_python = tmp_path / "fake-python.cmd"
    fake_python.write_text("@echo off\necho Python 3.11.16\n", encoding="utf-8")
    fake_uv = tmp_path / "fake-uv.cmd"
    fake_uv.write_text(
        "@echo off\n"
        'if "%FAKE_PYTHON_READY%"=="1" if "%1 %2"=="python find" echo %FAKE_PYTHON_PATH%\n',
        encoding="utf-8",
    )

    def literal(path: Path) -> str:
        return str(path).replace("'", "''")

    command = f"""
$PythonVersion = '3.11'
$PythonFallbackVersions = @('3.12', '3.10')
$TVenv = 30
$script:PythonExecutable = $null
$script:UvCmd = '{literal(fake_uv)}'
$env:FAKE_PYTHON_READY = '0'
$env:FAKE_PYTHON_PATH = '{literal(fake_python)}'
function Write-Info {{ param([string]$Message) }}
function Write-Err {{ param([string]$Message) }}
function Write-Warn {{ param([string]$Message) }}
function Write-Success {{ param([string]$Message) }}
function Invoke-WithTimeout {{
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec)
    $env:FAKE_PYTHON_READY = '1'
    return @{{ ExitCode = 0; TimedOut = $false }}
}}
{_powershell_function('Resolve-AvailablePythonVersion')}
{_powershell_function('Test-Python')}
$result = Test-Python
Write-Output "$result|$script:PythonExecutable"
"""
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip().splitlines()[-1] == f"True|{fake_python}"


def test_venv_creation_uses_the_resolved_python_executable():
    source = _powershell_function("Install-Deps")
    assert '$script:PythonExecutable' in source
    assert '$script:PythonVersion' not in source
    assert "Python was detected but its executable path was not recorded" in source
