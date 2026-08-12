"""When the installer offers the setup wizard.

Setup used to run only on a fresh install. Every run after the first printed
"Existing installation updated - skipping first-run setup", which left no way
to change a model, endpoint or workspace through the installer at all -- the
one run that offered the wizard was the run before you knew you needed it.

The wizard reads the existing config and offers each stored value back as the
default, so re-running it is not destructive: pressing Enter through the
prompts leaves the file as it was.

As in test_installer_progress, the installer body is never executed. Only the
function definitions are lifted from the parsed AST, so nothing here can clone
a repo, create a venv, or launch the real agent. Reaching the wizard is proved
by the message emitted when the executable is absent, which the skip path never
gets far enough to print.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="install.ps1 is the Windows installer")

INSTALLER = Path(__file__).resolve().parent.parent / "install.ps1"

_HARNESS = """
# Continue, matching the real installer: native commands write progress to
# stderr, and Stop turns every such line into a fatal NativeCommandError.
$ErrorActionPreference = "Continue"
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    "{installer}", [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({{
    $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }}, $true)) {{
    if (@("Run-InitialSetup", "Write-Info", "Write-Warn") -contains $fn.Name) {{
        . ([scriptblock]::Create($fn.Extent.Text))
    }}
}}
$script:FreshInstall = ${fresh}
$SkipSetup = ${skip}
$NonInteractive = ${noninteractive}
# No venv\\Scripts\\agent8088.exe here, so reaching the wizard surfaces as the
# "not ready yet" warning rather than actually launching anything.
$InstallDir = "{installdir}"
$script:InitialSetupRan = $false
Run-InitialSetup
"""


def _install_fake_agent(tmp_path):
    """A stand-in for agent8088.exe at the path the installer looks in.

    where.exe is a real, tiny PE that exits immediately on an argument it does
    not understand -- enough to get past Test-Path and reach the wizard branch
    without launching anything that could prompt or hang.
    """
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "where.exe",
                scripts / "agent8088.exe")


def _run_setup_stage(tmp_path, fresh=False, skip=False, noninteractive=False,
                     with_agent=False):
    if with_agent:
        _install_fake_agent(tmp_path)
    script = _HARNESS.format(
        installer=str(INSTALLER).replace("\\", "\\\\"),
        installdir=str(tmp_path).replace("\\", "\\\\"),
        fresh=str(fresh).lower(),
        skip=str(skip).lower(),
        noninteractive=str(noninteractive).lower(),
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"harness failed: {result.stderr[:400]}"
    return result.stdout


REACHED_WIZARD = "not ready yet"


def test_an_update_still_offers_setup(tmp_path):
    """The regression: an existing install skipped the wizard outright."""
    output = _run_setup_stage(tmp_path, fresh=False)

    assert REACHED_WIZARD in output, "an update skipped setup"
    assert "skipping" not in output.lower()


def test_a_fresh_install_still_offers_setup(tmp_path):
    output = _run_setup_stage(tmp_path, fresh=True)

    assert REACHED_WIZARD in output


def test_a_fresh_install_calls_it_first_run_setup(tmp_path):
    output = _run_setup_stage(tmp_path, fresh=True, with_agent=True)

    assert "first-run setup" in output.lower()


def test_an_update_says_that_existing_values_are_kept(tmp_path):
    """Re-running the wizard is safe, but only if the user is told so."""
    output = _run_setup_stage(tmp_path, fresh=False, with_agent=True)

    assert "keep the current value" in output.lower()
    assert "first-run" not in output.lower(), "an update is not a first run"


def test_skip_setup_still_opts_out(tmp_path):
    """Running the wizard on every update needs an escape hatch that works."""
    output = _run_setup_stage(tmp_path, fresh=False, skip=True)

    assert "skipping setup" in output.lower()
    assert REACHED_WIZARD not in output


def test_non_interactive_still_opts_out(tmp_path):
    """An unattended run must never block on a prompt."""
    output = _run_setup_stage(tmp_path, fresh=False, noninteractive=True)

    assert "non-interactive" in output.lower()
    assert REACHED_WIZARD not in output


def test_non_interactive_wins_on_a_fresh_install_too(tmp_path):
    output = _run_setup_stage(tmp_path, fresh=True, noninteractive=True)

    assert REACHED_WIZARD not in output


# ---------------------------------------------------------------------------
# the update instructions printed at the end
# ---------------------------------------------------------------------------
def test_the_update_command_does_not_name_a_feature_branch():
    """It pointed at feat/install-all-deps -- a merged branch that can be
    deleted at any time -- and printed that even to someone who had installed
    from main."""
    installer = INSTALLER.read_text(encoding="utf-8")

    # The URL form, not the bare name: the comment explaining the fix mentions
    # the old branch, and should not have to avoid saying what it fixed.
    assert "AGENT8088/feat/install-all-deps" not in installer
    assert 'AGENT8088/$Branch/install.ps1' in installer
