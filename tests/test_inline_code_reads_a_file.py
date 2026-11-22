"""A written file named inside inline interpreter code is read by that code, not run.

Seen in a benchmark run (render-report-page): `powershell -NoProfile -Command "$h = Get-Content -Raw '<ws>\\report.html';
..."` pulled the rows and bars out of the page the run wrote and failed on a path. Because "powershell" and
"report.html" were on one line it counted as running the page, so the finished page was reported as "the latest check
failed" and the run, which the grader passed, ended NOT VERIFIED."""
import pytest

from agent8088.trajectory import TrajectoryState, _runs_a_written_file

PAGE = r"E:\bench\render-report-page_2\workspace\report.html"
READ = (r"""powershell -NoProfile -Command "$h = Get-Content -Raw 'E:\bench\render-report-page_2\workspace\report.html'; """
        r"""$rows = [regex]::Matches($h, '<tr data-month'); Write-Output ('rows: ' + $rows.Count)" """)


@pytest.mark.parametrize("command", [
    READ,
    "python -c \"print(open('report.html').read()[:200])\"",
    "node -e \"console.log(require('fs').readFileSync('report.html','utf8').length)\"",
    "pwsh -c \"(Get-Content report.html).Count\"",
])
def test_reading_a_written_file_in_inline_code_is_not_running_it(command):
    assert not _runs_a_written_file(command, [PAGE])


@pytest.mark.parametrize("command,written", [
    ("python report.py", ["report.py"]),
    ("node dist\\app.js", ["app.js"]),
    ("powershell -File build.ps1", ["build.ps1"]),
    ("powershell -NoProfile -ExecutionPolicy Bypass -File .\\build.ps1", ["build.ps1"]),
    ("bash -c \"python app.py\"", ["app.py"]),
    ("cd /d C:\\w && python server.py --port 8000", ["server.py"]),
])
def test_running_a_written_file_is_still_running_it(command, written):
    assert _runs_a_written_file(command, written)


def test_the_run_from_the_benchmark_no_longer_leaves_a_failed_check():
    state = TrajectoryState({}, goal="g", message_count=1)
    write = state.before_tool("write_file")
    state.after_tool(write, "Wrote 4096 bytes", failed=False, blocked=False, mutated=True, verdict="unknown", path=PAGE)
    shell = state.before_tool("execute_shell")
    state.after_tool(shell, "The system cannot find the file specified.\nCommand exited with status 1.", failed=True,
                     blocked=False, mutated=False, verdict="failed", command=READ)
    assert not state.check_failing()


def test_a_written_script_that_crashes_is_still_a_failed_check(tmp_path):
    # A path of this platform: on Linux a Windows path is one file name, backslashes included.
    state = TrajectoryState({}, goal="g", message_count=1)
    write = state.before_tool("write_file")
    state.after_tool(write, "Wrote 300 bytes", failed=False, blocked=False, mutated=True, verdict="unknown",
                     path=str(tmp_path / "report.py"))
    shell = state.before_tool("execute_shell")
    state.after_tool(shell, "Traceback (most recent call last):\nKeyError: 'month'\nCommand exited with status 1.",
                     failed=True, blocked=False, mutated=False, verdict="failed", command="python report.py")
    assert state.check_failing()
