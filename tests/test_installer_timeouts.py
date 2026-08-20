"""install.ps1's timeout helpers, exercised in isolation.

Follows the convention already used by test_installer_terminal.py: regex one
function out of install.ps1 and run just that function under pwsh/powershell. That
keeps these runnable on a developer machine without performing an install.

WHAT THIS CANNOT COVER. On macOS/Linux the available host is PowerShell 7 (Core), so
every `if` that branches on edition takes the Core path. Two things therefore need
real Windows PowerShell 5.1 and are marked skipif rather than left to pass
vacuously -- a vacuous pass reads as coverage that does not exist:

  * the manual argv-quoting branch (5.1's .NET Framework ProcessStartInfo has no
    ArgumentList, so the command line is built by hand)
  * ServicePointManager.SecurityProtocol defaulting to TLS 1.0, which is the whole
    reason the TLS block exists (7.x negotiates via the OS and ignores it)
"""
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARPIT = ROOT / "tests" / "support" / "tarpit.py"


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
    stubs = 'function Write-Warn { param([string]$Message) Write-Output $Message }\n'
    script = stubs + "\n".join(_powershell_function(f) for f in functions) + "\n" + body
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", script],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _is_desktop_edition() -> bool:
    """True only on Windows PowerShell 5.1."""
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if not ps:
        return False
    out = subprocess.run([ps, "-NoProfile", "-Command", "$PSVersionTable.PSEdition"],
                         capture_output=True, text=True)
    return out.stdout.strip() == "Desktop"


needs_win_ps = pytest.mark.skipif(
    not _is_desktop_edition(),
    reason="needs Windows PowerShell 5.1 (Desktop edition); pwsh 7 takes the other branch",
)

# /bin/sleep on POSIX, ping.exe on Windows. Kept in one place so the assertions
# below read the same on both.
if sys.platform == "win32":
    SLEEP, SLEEP_ARGS = "ping.exe", '@("-n", "61", "127.0.0.1")'
    ECHO, SH = "cmd.exe", "cmd.exe"
    EXIT7 = '-FilePath "cmd.exe" -Arguments @("/c", "exit 7")'
    EXIT0 = '-FilePath "cmd.exe" -Arguments @("/c", "exit 0")'
    ECHO_SPACED = '-FilePath "cmd.exe" -Arguments @("/c", "echo", "a gent 8088")'
else:
    SLEEP, SLEEP_ARGS = "/bin/sleep", '@("60")'
    EXIT7 = '-FilePath "/bin/sh" -Arguments @("-c", "exit 7")'
    EXIT0 = '-FilePath "/bin/sh" -Arguments @("-c", "exit 0")'
    ECHO_SPACED = '-FilePath "/bin/echo" -Arguments @("a gent 8088")'


# --------------------------------------------------------------------------
# Invoke-WithTimeout
# --------------------------------------------------------------------------
def test_a_hang_is_killed_and_reported_as_a_hang():
    started = time.monotonic()
    out = _run(
        f'$r = Invoke-WithTimeout -FilePath "{SLEEP}" -Arguments {SLEEP_ARGS} -TimeoutSec 3\n'
        'Write-Output "TimedOut=$($r.TimedOut)"',
        "Invoke-WithTimeout",
    )
    elapsed = time.monotonic() - started
    assert "TimedOut=True" in out
    # A pass at ~60s would mean the call returned on the child's own exit, not on
    # the timeout -- which is the bug, not the fix.
    assert elapsed < 25, f"took {elapsed:.1f}s; the timeout did not fire"


def test_a_real_exit_code_is_not_flattened_to_zero():
    """Start-Process -PassThru did not reliably surface .ExitCode on a redirected
    child, so every stage read 0 and every failure looked like success."""
    out = _run(f'$r = Invoke-WithTimeout {EXIT7} -TimeoutSec 15\n'
               'Write-Output "ExitCode=$($r.ExitCode)"', "Invoke-WithTimeout")
    assert "ExitCode=7" in out


def test_success_is_zero_and_not_a_timeout():
    out = _run(f'$r = Invoke-WithTimeout {EXIT0} -TimeoutSec 15\n'
               'Write-Output "ExitCode=$($r.ExitCode) TimedOut=$($r.TimedOut)"',
               "Invoke-WithTimeout")
    assert "ExitCode=0" in out and "TimedOut=False" in out


def test_an_argument_containing_spaces_round_trips():
    """Install paths routinely contain spaces (C:\\Users\\First Last\\...)."""
    out = _run(f'$r = Invoke-WithTimeout {ECHO_SPACED} -TimeoutSec 15 -CaptureOutput\n'
               'Write-Output $r.Output', "Invoke-WithTimeout")
    assert "a gent 8088" in out


@needs_win_ps
def test_spaced_argument_round_trips_through_the_51_quoting_branch():
    """Same assertion, but only meaningful on Desktop edition: 5.1 has no
    ArgumentList and builds the command line by hand."""
    out = _run(f'$r = Invoke-WithTimeout {ECHO_SPACED} -TimeoutSec 15 -CaptureOutput\n'
               'Write-Output $r.Output', "Invoke-WithTimeout")
    assert "a gent 8088" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell loop")
def test_a_chatty_child_does_not_deadlock():
    """A child that fills its stdout buffer while nobody reads it blocks forever,
    which would reintroduce the exact hang this function prevents. npm is chatty
    enough to hit it, so both pipes are drained asynchronously."""
    loop = 'i=0; while [ $i -lt 20000 ]; do echo chatty-line-of-output-$i; i=$((i+1)); done'
    out = _run(f'$r = Invoke-WithTimeout -FilePath "/bin/sh" -Arguments @("-c", \'{loop}\') '
               '-TimeoutSec 40 -CaptureOutput\n'
               'Write-Output "ExitCode=$($r.ExitCode) Bytes=$($r.Output.Length)"',
               "Invoke-WithTimeout")
    assert "ExitCode=0" in out
    assert int(re.search(r"Bytes=(\d+)", out).group(1)) > 100_000


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows handle inheritance")
def test_a_grandchild_holding_the_pipe_does_not_hang_the_call():
    """The reported hang: the installer stopped with no output at all, right before
    the first message of the stage after npm.

    Both the post-exit WaitForExit() and a .GetResult() on the read task complete
    on pipe EOF, not on the child exiting - and the child's children inherit those
    pipe handles. One surviving grandchild (`ollama list` leaving a server behind)
    holds the write end open, so EOF never arrives and an unbounded wait sits
    inside the one function whose job is to bound waits. Here the child exits
    immediately and leaves a 40s grandchild behind; the call must not wait on it.
    """
    # Timed inside PowerShell: the grandchild inherits every inheritable handle,
    # this harness's own stdout pipe included, so subprocess.run here waits for it
    # no matter how promptly the function returns. Only the inner clock is the
    # measurement.
    spawn = "start /b ping.exe -n 21 127.0.0.1 & exit 0"
    out = _run(
        '$sw = [Diagnostics.Stopwatch]::StartNew()\n'
        f'$r = Invoke-WithTimeout -FilePath "cmd.exe" -Arguments @("/c", \'{spawn}\') '
        '-TimeoutSec 60 -CaptureOutput -DrainSec 2\n'
        '$sw.Stop()\n'
        'Write-Output "ExitCode=$($r.ExitCode) TimedOut=$($r.TimedOut) '
        'Elapsed=$([int]$sw.Elapsed.TotalSeconds)"',
        "Invoke-WithTimeout",
    )
    assert "TimedOut=False" in out, out
    inner = int(re.search(r"Elapsed=(\d+)", out).group(1))
    # Returning at ~20s would mean it waited for the grandchild, which is the bug.
    assert inner < 10, f"the call took {inner}s; it waited on the grandchild"


def test_the_drain_wait_is_bounded_at_every_call_site():
    """An argument-less WaitForExit() or a .GetResult() on the read task is an
    unbounded wait, whatever the stage budget says."""
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    code = [l for l in source.splitlines() if not l.strip().startswith("#")]
    offenders = [l.strip() for l in code
                 if re.search(r"WaitForExit\(\s*\)", l) or "GetAwaiter()" in l]
    assert offenders == [], f"unbounded wait(s) remain: {offenders}"


def test_a_missing_binary_returns_a_failure_rather_than_throwing():
    """A provider of last resort must not raise; the installer decides what to do."""
    out = _run('$r = Invoke-WithTimeout -FilePath "/definitely/not/here" -TimeoutSec 5\n'
               'Write-Output "ExitCode=$($r.ExitCode) TimedOut=$($r.TimedOut)"',
               "Invoke-WithTimeout")
    assert "ExitCode=-1" in out and "TimedOut=False" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_working_directory_applies_to_the_child_only():
    """The WhatsApp bridge stage needs the child's cwd changed without moving the
    caller's location."""
    out = _run('$before = (Get-Location).Path\n'
               '$r = Invoke-WithTimeout -FilePath "/bin/pwd" -TimeoutSec 15 '
               '-WorkingDirectory "/tmp" -CaptureOutput\n'
               'Write-Output "child=$($r.Output.Trim()) callerMoved=$((Get-Location).Path -ne $before)"',
               "Invoke-WithTimeout")
    assert "tmp" in out
    assert "callerMoved=False" in out


# --------------------------------------------------------------------------
# Invoke-BoundedDownload -- the stalled-body case
# --------------------------------------------------------------------------
@pytest.fixture
def tarpit():
    proc = subprocess.Popen([sys.executable, str(TARPIT)],
                            stdout=subprocess.PIPE, text=True)
    port = proc.stdout.readline().strip()
    assert port.isdigit(), f"tarpit did not report a port: {port!r}"
    yield f"http://127.0.0.1:{port}/big.bin"
    proc.kill()
    proc.wait(timeout=10)


def test_a_stalled_body_is_cut_off(tarpit, tmp_path):
    """The measurement this whole function exists for. Invoke-WebRequest -OutFile
    -TimeoutSec 5 against this same server runs indefinitely: -TimeoutSec covers
    headers only on 5.1 and stops applying once the stream copy begins on 7.x."""
    out_file = tmp_path / "stalled.bin"
    started = time.monotonic()
    out = _run(f'$d = Invoke-BoundedDownload -Uri "{tarpit}" -OutFile "{out_file}" -TimeoutSec 5\n'
               'Write-Output "TimedOut=$($d.TimedOut) Success=$($d.Success)"',
               "Invoke-BoundedDownload")
    elapsed = time.monotonic() - started
    assert "TimedOut=True" in out
    assert "Success=False" in out
    assert elapsed < 30, f"took {elapsed:.1f}s; the body transfer was not bounded"


def test_a_cut_off_download_leaves_no_partial_file(tarpit, tmp_path):
    """Every caller's next step is Expand-Archive or a self-extractor, and a partial
    archive fails there with a corruption error that names the wrong cause."""
    out_file = tmp_path / "stalled.bin"
    _run(f'$d = Invoke-BoundedDownload -Uri "{tarpit}" -OutFile "{out_file}" -TimeoutSec 5\n'
         'Write-Output "done"', "Invoke-BoundedDownload")
    assert not out_file.exists()


def test_a_real_download_still_succeeds(tmp_path):
    out_file = tmp_path / "real.json"
    out = _run('$d = Invoke-BoundedDownload -Uri "https://api.github.com/zen" '
               f'-OutFile "{out_file}" -TimeoutSec 30\n'
               'Write-Output "Success=$($d.Success) Error=$($d.Error)"',
               "Invoke-BoundedDownload")
    if "Success=True" not in out:
        pytest.skip(f"network unavailable: {out.strip()}")
    assert out_file.exists() and out_file.stat().st_size > 0


def test_an_http_error_is_reported_not_thrown(tmp_path):
    out_file = tmp_path / "missing.bin"
    out = _run('$d = Invoke-BoundedDownload '
               '-Uri "https://api.github.com/this-does-not-exist-8088" '
               f'-OutFile "{out_file}" -TimeoutSec 20\n'
               'Write-Output "Success=$($d.Success) TimedOut=$($d.TimedOut) Error=$($d.Error)"',
               "Invoke-BoundedDownload")
    if "Error=" not in out:
        pytest.skip("network unavailable")
    assert "Success=False" in out
    assert "TimedOut=False" in out, "an HTTP error must not be reported as a hang"
    assert not out_file.exists()


# --------------------------------------------------------------------------
# Budgets -- the agreed limits, asserted so a later edit cannot drift them
# --------------------------------------------------------------------------
@pytest.mark.parametrize(("name", "seconds"), [
    ("TOllamaCheck", 15),    # nothing, local
    ("TOllamaPull", 600),    # 274 MB embedding model
    ("TNpm", 300),           # 142 small packages
    ("TChromium", 600),      # ~150 MB browser
    ("TDownload", 180),      # ~30 MB archives
    ("TPip", 300),           # gateway extras
])
def test_windows_budget_matches_the_agreed_table(name, seconds):
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"^\${name}\s*=\s*(\d+)\s*\*\s*\$TimeoutScale", source, re.M)
    assert match, f"budget not found: ${name}"
    assert int(match.group(1)) == seconds


@pytest.mark.parametrize(("name", "seconds"), [
    ("T_OLLAMA_CHECK", 15),
    ("T_OLLAMA_PULL", 600),
    ("T_NPM", 300),
    ("T_CHROMIUM", 600),
    ("T_NODE_DL", 180),
    ("T_PIP", 300),
    ("T_GIT", 600),
])
def test_unix_budget_matches_the_agreed_table(name, seconds):
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(rf"^{name}=\$\(\((\d+)\s*\*\s*TIMEOUT_SCALE\)\)", source, re.M)
    assert match, f"budget not found: {name}"
    assert int(match.group(1)) == seconds


def test_no_unbounded_download_remains_in_install_ps1():
    """Invoke-WebRequest -OutFile cannot be bounded on either edition, so no call
    site may remain. Start-Process -Wait is likewise unbounded."""
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")
    code = [l for l in source.splitlines() if not l.strip().startswith("#")]
    offenders = [l.strip() for l in code
                 if "Invoke-WebRequest" in l or ("Start-Process" in l and "-Wait" in l)]
    assert offenders == [], f"unbounded transfer(s) remain: {offenders}"
