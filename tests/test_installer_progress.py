"""The installer's progress bar, exercised as PowerShell rather than as text.

Every long stage used to be piped to Out-Null, so the console sat on a single
line for minutes -- Chromium alone is ~280 MB -- with no way to tell a slow
download from a hang, and the child's diagnostics were discarded along with its
output.

These run the real functions out of install.ps1. The installer body is never
executed: only the function definitions are lifted from the parsed AST, so
loading them cannot clone a repo, create a venv or download anything. The child
process is always a throwaway `python -c`.

Text assertions would not have earned their keep here. Two defects survived
review of this code and were caught only by running it:

  * Start-Process -PassThru leaves .ExitCode as $null unless the process handle
    is touched first, so every stage reported failure and returned nothing.
  * -ArgumentList joins an array without quoting, so the first path containing
    a space arrived at the child split into separate arguments -- and every one
    of these paths comes from $env:LOCALAPPDATA, which contains a space
    whenever the account name does.
"""
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="install.ps1 is the Windows installer")

INSTALLER = Path(__file__).resolve().parent.parent / "install.ps1"

# Lift the named functions out of the AST and declare them. Parsing never runs
# the script, so none of the installer's stages execute.
_HARNESS = """
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    "{installer}", [ref]$null, [ref]$null)
$wanted = @("Test-ProgressAnimated", "Format-ProgressBar", "Format-ProgressSweep",
            "Get-ReportedPercent", "Invoke-WithProgress", "ConvertTo-ArgumentString",
            "Write-Info", "Write-Warn")
foreach ($fn in $ast.FindAll({{
    $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }}, $true)) {{
    if ($wanted -contains $fn.Name) {{ . ([scriptblock]::Create($fn.Extent.Text)) }}
}}
$script:ProgressBarWidth = 24
{body}
"""


def _powershell(body):
    """Run *body* with the installer's progress functions in scope."""
    script = _HARNESS.format(installer=str(INSTALLER).replace("\\", "\\\\"), body=body)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"harness failed: {result.stderr[:400]}"
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# the bar itself
# ---------------------------------------------------------------------------
def test_the_bar_fills_in_proportion_to_the_percentage():
    assert _powershell("Write-Output (Format-ProgressBar 0)") == "[" + "." * 24 + "]"
    assert _powershell("Write-Output (Format-ProgressBar 50)") == "[" + "#" * 12 + "." * 12 + "]"
    assert _powershell("Write-Output (Format-ProgressBar 100)") == "[" + "#" * 24 + "]"


def test_an_out_of_range_percentage_cannot_deform_the_bar():
    """A malformed line from a child must not draw a bar wider than the field."""
    assert _powershell("Write-Output (Format-ProgressBar 999)") == "[" + "#" * 24 + "]"
    assert _powershell("Write-Output (Format-ProgressBar -20)") == "[" + "." * 24 + "]"


def test_the_indeterminate_sweep_keeps_the_bar_width_constant():
    """The line must not change shape when a percentage finally appears."""
    widths = _powershell(
        "0..40 | ForEach-Object { (Format-ProgressSweep $_).Length } | Sort-Object -Unique"
    ).split()
    assert widths == [str(len("[" + "." * 24 + "]"))]


# ---------------------------------------------------------------------------
# reading progress back out of the child's output
# ---------------------------------------------------------------------------
def test_a_reported_percentage_is_read_from_the_childs_output(tmp_path):
    log = tmp_path / "child.log"
    log.write_text("Downloading Chromium\n|####    | 42% of 280 MB\n", encoding="utf-8")

    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{log}") -Fallback -1)') == "42"


def test_the_bar_never_runs_backwards(tmp_path):
    """A tail can straddle two bars: pip finishing one package as another starts."""
    log = tmp_path / "child.log"
    log.write_text("|##  | 5% of the next file\n", encoding="utf-8")

    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{log}") -Fallback 80)') == "80"


def test_output_with_no_percentage_leaves_the_bar_indeterminate(tmp_path):
    log = tmp_path / "child.log"
    log.write_text("Resolving dependencies\n", encoding="utf-8")

    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{log}") -Fallback -1)') == "-1"


def test_a_log_the_child_still_holds_open_is_survivable(tmp_path):
    """Polling runs ~8x a second against a file the child is writing."""
    assert _powershell(
        f'Write-Output (Get-ReportedPercent -Paths @("{tmp_path / "absent.log"}") -Fallback 7)'
    ) == "7"


# ---------------------------------------------------------------------------
# running an actual child process
# ---------------------------------------------------------------------------
def _run_child(exit_code, extra=""):
    """Invoke-WithProgress against a throwaway python child, animation forced on.

    Output is redirected under pytest, which would otherwise select the plain
    fallback and leave the animated path untested.
    """
    return _powershell(
        'function Test-ProgressAnimated { $true }\n'
        f'$code = Invoke-WithProgress -Label "test" -FilePath "{sys.executable}" '
        f'-ArgumentList @("-c", "import sys; sys.exit({exit_code})"{extra})\n'
        'Write-Output "EXIT=$code"'
    ).splitlines()[-1]


def test_a_successful_stage_reports_success():
    """Start-Process -PassThru returns $null for .ExitCode unless the handle is
    cached first, which made every stage look like a failure."""
    assert _run_child(0) == "EXIT=0"


def test_a_failing_stage_propagates_the_childs_exit_code():
    """Callers branch on this to warn-and-continue or abort."""
    assert _run_child(3) == "EXIT=3"


def test_an_argument_containing_a_space_survives_intact(tmp_path):
    """LOCALAPPDATA contains a space whenever the account name does."""
    spaced = tmp_path / "dir with space"
    spaced.mkdir()
    marker = spaced / "marker.txt"
    quoted = str(marker).replace("\\", "\\\\").replace('"', '')

    out = _powershell(
        'function Test-ProgressAnimated { $true }\n'
        f'$code = Invoke-WithProgress -Label "test" -FilePath "{sys.executable}" '
        f"-ArgumentList @(\"-c\", \"open(r'{marker}','w').write('ok')\")\n"
        'Write-Output "EXIT=$code"'
    )

    assert out.splitlines()[-1] == "EXIT=0"
    assert marker.read_text() == "ok", "the path was split on its space"
    assert quoted  # the path really did contain a space


def test_a_missing_executable_does_not_abort_the_install():
    """These stages are optional; the progress display must never be fatal."""
    out = _powershell(
        'function Test-ProgressAnimated { $true }\n'
        '$code = Invoke-WithProgress -Label "test" -FilePath "C:\\nope\\missing.exe" '
        '-ArgumentList @("x")\n'
        'Write-Output "EXIT=$code"'
    )

    assert out.splitlines()[-1] != "EXIT=0"


def test_the_plain_fallback_runs_the_command_too():
    """Redirected output (CI, a log file) must still install, just without a bar."""
    out = _powershell(
        'function Test-ProgressAnimated { $false }\n'
        f'$code = Invoke-WithProgress -Label "test" -FilePath "{sys.executable}" '
        '-ArgumentList @("-c", "import sys; sys.exit(0)")\n'
        'Write-Output "EXIT=$code"'
    )

    assert out.splitlines()[-1] == "EXIT=0"


# ---------------------------------------------------------------------------
# the stages are actually wired to it
# ---------------------------------------------------------------------------
def test_the_long_stages_no_longer_swallow_their_output():
    """A helper nothing calls would leave the console just as silent."""
    installer = INSTALLER.read_text(encoding="utf-8")
    for stage in ("Chromium", "gateway", "search", "browser"):
        assert f"[{stage}]" in installer or stage in installer

    for silenced in ("playwright install chromium 2>&1 | Out-Null",
                     '-e "$InstallDir[gateway]" 2>&1 | Out-Null',
                     '-e "$InstallDir[browser]" 2>&1 | Out-Null'):
        assert silenced not in installer, f"still discarding output: {silenced}"

    assert installer.count("Invoke-WithProgress -Label") >= 6
