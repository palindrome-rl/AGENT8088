import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _powershell_function(name: str) -> str:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$",
        source,
    )
    assert match, f"PowerShell function not found: {name}"
    return match.group(0)


@pytest.mark.parametrize(
    ("fresh_install", "config_created", "expected"),
    [
        (False, False, "Existing installation and config found"),
        (False, True, "Non-interactive mode - skipping first-run setup"),
        (True, False, "Non-interactive mode - skipping first-run setup"),
    ],
)
def test_windows_setup_runs_for_fresh_or_resumed_install(fresh_install, config_created, expected):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is not installed")

    function = _powershell_function("Run-InitialSetup")
    command = f"""
function Write-Info {{ param([string]$Message) Write-Output $Message }}
{function}
$script:FreshInstall = ${str(fresh_install).lower()}
$script:ConfigCreated = ${str(config_created).lower()}
$SkipSetup = $false
$NonInteractive = $true
Run-InitialSetup
"""
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )
    assert expected in result.stdout


def test_windows_new_default_config_marks_setup_required():
    function = _powershell_function("Drop-Config")
    assert "$script:ConfigCreated = $true" in function


def test_unix_new_default_config_marks_setup_required():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "CONFIG_CREATED=true" in source
    assert '[ "$FRESH_INSTALL" != true ] && [ "$CONFIG_CREATED" != true ]' in source
