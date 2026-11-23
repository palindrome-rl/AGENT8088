"""A command whose program path has a space and no quotes never started: the shell cut the path at the space.

Seen in one benchmark run: `E:\\My Work\\.venv\\Scripts\\python.exe -c "..."` failed with "'E:\\My' is not recognized
as an internal or external command", and the run ended "the latest check failed" over work that passed every grader
check. A quoted path that is really missing, and a real failure of the same check, still count.
"""
import pytest

from agent8088.trajectory import TrajectoryState, _cut_program_path

PY = "E:\\My Work\\.venv\\Scripts\\python.exe"
CUT = ("'E:\\My' is not recognized as an internal or external command,\noperable program or batch file.\n"
       "Command exited with status 1.")


@pytest.mark.parametrize("command, output", [
    (f'{PY} -c "import sys; print(sys.version)"', CUT),
    (f"cd app && {PY} check.py", CUT),
    ("/c/My Work/.venv/bin/python check.py", "sh: 1: /c/My: not found\nCommand exited with status 127."),
    (f"{PY} check.py", "The term 'E:\\My' is not recognized as the name of a cmdlet, function, script file"),
])
def test_a_program_path_cut_at_a_space_did_not_start(command, output):
    assert _cut_program_path(command, output)


def test_a_quoted_or_unrelated_missing_program_is_not_one():
    assert not _cut_program_path(f'"{PY}" check.py', CUT)
    assert not _cut_program_path("python check.py", CUT)
    assert not _cut_program_path(f"{PY} check.py", CUT + "\n2 tests failed")


def _run(state, command, result):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict="failed", command=command)


def test_the_cut_command_is_not_a_failing_check():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = ["E:\\w\\game.html"]
    _run(state, f'{PY} -c "import playwright"', CUT)
    assert not state.check_failing()
    assert state.environment_note() == ""


def test_a_real_failure_after_it_still_counts():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = ["E:\\w\\check.py"]
    _run(state, f'{PY} check.py', CUT)
    _run(state, f'"{PY}" check.py', "Traceback (most recent call last):\nAssertionError: 3 != 4\n"
                                     "Command exited with status 1.")
    assert state.check_failing()
