"""First-run setup must be offered on EVERY installer run.

Rewritten from a version that asserted the opposite. It pinned the
`FRESH_INSTALL != true && CONFIG_CREATED != true` gate, and that gate is the bug:
when an optional stage fails the core agent still installs, so the user re-runs the
installer -- and got no prompt for working directory, model or web search, and no
hint that `agent8088 --setup` exists. The old expectations are kept below as
explicit "must NOT happen" assertions so the regression cannot come back quietly.
"""
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
    return result.stdout


def _harness(body: str, *, skip_setup=False, non_interactive=True,
             fresh_install=False, config_created=False) -> str:
    """Run Run-InitialSetup in isolation with the surrounding state stubbed.

    FreshInstall/ConfigCreated are still set even though the function no longer
    reads them -- that is the point of test_setup_ignores_the_fresh_install_flags.
    """
    return f"""
function Write-Info {{ param([string]$Message) Write-Output $Message }}
function Write-Warn {{ param([string]$Message) Write-Output $Message }}
function Register-SkippedStage {{ param($Label, $Reason, $Fix) Write-Output "SKIPREC:$Label" }}
{_powershell_function("Run-InitialSetup")}
$script:FreshInstall  = ${str(fresh_install).lower()}
$script:ConfigCreated = ${str(config_created).lower()}
$SkipSetup            = ${str(skip_setup).lower()}
$NonInteractive       = ${str(non_interactive).lower()}
$InstallDir           = "/definitely/not/a/real/install"
{body}
"""


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("fresh_install", "config_created"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_setup_ignores_the_fresh_install_flags(fresh_install, config_created):
    """Every combination must reach the same place. Previously (False, False) took
    an early "Existing installation and config found" return."""
    out = _run_powershell(_harness(
        "Run-InitialSetup",
        fresh_install=fresh_install, config_created=config_created,
    ))
    assert "Existing installation and config found" not in out
    # With $NonInteractive the run stops at the interactivity gate, which is the
    # only remaining reason to skip other than an explicit -SkipSetup.
    assert "Non-interactive mode" in out


def test_skip_setup_is_still_honoured():
    out = _run_powershell(_harness("Run-InitialSetup", skip_setup=True))
    assert "Skipping setup" in out
    assert "agent8088 --setup" in out, "must name the manual command"


def test_non_interactive_names_the_manual_command():
    out = _run_powershell(_harness("Run-InitialSetup", non_interactive=True))
    assert "agent8088 --setup" in out


def test_an_unrunnable_agent_is_recorded_not_silently_skipped():
    """A missing binary used to warn and vanish. It now lands in the end-of-run
    ledger, because a warning mid-install scrolls out of view."""
    out = _run_powershell(_harness("Run-InitialSetup", non_interactive=False))
    assert "SKIPREC:First-run setup" in out


def test_windows_setup_has_a_venv_interpreter_fallback():
    """A missing .exe shim (partial install, AV quarantine) is not a reason to skip
    setup when the module itself is importable."""
    function = _powershell_function("Run-InitialSetup")
    assert "agent8088.cli --setup" in function
    assert "python.exe" in function


def test_windows_setup_is_not_wrapped_in_a_timeout():
    """Setup is interactive and reads the console; a wall clock would kill the user
    mid-answer."""
    function = _powershell_function("Run-InitialSetup")
    assert "Invoke-WithTimeout" not in function


def test_windows_setup_no_longer_reads_the_fresh_install_flags():
    function = _powershell_function("Run-InitialSetup")
    assert "FreshInstall" not in function
    assert "ConfigCreated" not in function


def test_windows_new_default_config_marks_setup_required():
    """Drop-Config still sets the flag. It is no longer a setup gate, but
    Verify-Install and Start-InitialAgent still report on it."""
    function = _powershell_function("Drop-Config")
    assert "$script:ConfigCreated = $true" in function


# --------------------------------------------------------------------------
# Unix
# --------------------------------------------------------------------------
def _unix_setup_body() -> str:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^run_initial_setup\(\) \{.*?^\}$", source)
    assert match, "run_initial_setup not found in install.sh"
    return match.group(0)


def test_unix_setup_no_longer_gates_on_fresh_install():
    body = _unix_setup_body()
    assert "FRESH_INSTALL" not in body
    assert "CONFIG_CREATED" not in body
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert '[ "$FRESH_INSTALL" != true ] && [ "$CONFIG_CREATED" != true ]' not in source


def test_unix_new_default_config_still_marks_setup_required():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "CONFIG_CREATED=true" in source


def test_unix_setup_keeps_only_the_two_impossible_to_prompt_gates():
    body = _unix_setup_body()
    assert 'SKIP_SETUP" = true' in body
    assert "/dev/tty" in body


def test_unix_setup_has_a_venv_interpreter_fallback():
    body = _unix_setup_body()
    assert "agent8088.cli --setup" in body
    assert "venv/bin/python" in body


def test_unix_setup_records_a_skip_in_the_ledger():
    body = _unix_setup_body()
    assert body.count("record_skip") == 2, "both failure paths must be recorded"


def test_unix_setup_runs_through_the_tty_helper():
    """run_agent8088_command feeds the child < /dev/tty, which is what makes
    prompting work under `curl | bash`."""
    body = _unix_setup_body()
    assert "run_agent8088_command" in body
