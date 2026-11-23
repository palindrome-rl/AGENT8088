"""A check whose program is not on the PATH, or whose script is not at the path the command gave, never started.

Seen in one benchmark run: `node _verify_game_logic.js` ran in a shell with no node ("sh: 1: node: not found"), and
`python _verify.py` named the script at the workspace root while it sat under artifacts/. Both runs ended "the latest
check failed" over work that passed every grader check; in the second the same script then passed twice from the
right path. A module the work imports, a file the script opens, and a real test failure are still failures.
"""
import pytest

from agent8088.trajectory import TrajectoryState, _program_missing, _script_missing

WS = "C:\\w\\proj"
NO_NODE = "sh: 1: node: not found\nCommand exited with status 127."
NO_SCRIPT = ("C:\\py\\python.exe: can't open file 'C:\\\\w\\\\proj\\\\_verify.py': [Errno 2] No such file or "
             "directory\nCommand exited with status 2.")


def _state(written=("artifacts\\_verify.py", "artifacts\\_verify_game_logic.js")):
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = [f"{WS}\\{name}" for name in written]
    return state


def _run(state, command, result, verdict="failed"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


@pytest.mark.parametrize("command, output, name", [
    ("node _verify_game_logic.js", NO_NODE, "node"),
    ("cd app && node test.js", "/bin/sh: line 1: node: command not found\nCommand exited with status 127.", "node"),
    ("npx playwright test", "'npx' is not recognized as an internal or external command,\noperable program or batch "
                            "file.", "npx"),
    ("powershell -File check.ps1", "sh: 1: powershell: not found", "powershell"),
])
def test_a_missing_program_is_named(command, output, name):
    assert _program_missing(command, output) == f"the program '{name}' is not available where the check ran"


def test_a_missing_program_the_command_does_not_start_is_not_one():
    # The test itself shelled out to something missing: that is the work's failure, not the check's.
    assert _program_missing("python test_cli.py", "sh: 1: ffmpeg: not found\nCommand exited with status 1.") == ""
    assert _program_missing("node test.js", "sh: 1: node: not found\n2 tests failed") == ""


def test_a_script_that_is_not_there_did_not_start():
    assert _script_missing('".venv\\Scripts\\python.exe" _verify.py', NO_SCRIPT)
    assert _script_missing("node verify.mjs", "Error: Cannot find module 'C:\\w\\proj\\verify.mjs'\n"
                                              "    at Module._resolveFilename\n  code: 'MODULE_NOT_FOUND'")


def test_a_file_the_script_opens_is_a_real_failure():
    assert not _script_missing("python report.py", "FileNotFoundError: [Errno 2] No such file or directory: 'sales.csv'")
    assert not _script_missing("node app.test.js", "Error: Cannot find module './cart.js'\nRequire stack:\n- app.test.js")


def test_a_check_with_no_program_is_not_a_failing_check_but_is_noted():
    state = _state()
    _run(state, "node _verify_game_logic.js", NO_NODE)
    assert not state.check_failing()
    assert "the program 'node' is not available where the check ran" in state.environment_note()


def test_a_check_whose_script_was_not_there_is_not_a_failing_check():
    state = _state()
    _run(state, '".venv\\Scripts\\python.exe" _verify.py', NO_SCRIPT)
    assert not state.check_failing()
    assert state.environment_note() == ""


def test_a_real_failure_of_the_same_check_still_counts():
    state = _state()
    _run(state, '".venv\\Scripts\\python.exe" artifacts\\_verify.py',
         "Traceback (most recent call last):\n  File \"_verify.py\", line 9\nAssertionError: totals differ\n"
         "Command exited with status 1.")
    assert state.check_failing()
