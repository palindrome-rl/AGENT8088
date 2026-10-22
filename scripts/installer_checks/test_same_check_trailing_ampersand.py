"""A failed check is settled when the same check runs clean later, even if the failing command line had an
extra clause after a lone `&` (whose exit status cmd.exe reports instead of the check's)."""
import pytest

from agent8088.trajectory import TrajectoryState, _same_command

COMPILE = r'cd E:\app && python -m py_compile server.py && echo SERVER_PY_OK'
WITH_DIR = COMPILE + r' & dir _bindtest.py _dbg.js 2>nul'


def _record(state, verdict, command, result="x"):
    op = state.before_tool("execute_shell")
    state.after_tool(op, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


def test_the_check_without_the_tacked_on_clause_is_the_same_command():
    assert _same_command(WITH_DIR, COMPILE)
    assert _same_command(COMPILE, COMPILE)
    assert _same_command(COMPILE.upper(), COMPILE)


def test_it_does_not_work_the_other_way_round():
    # `check & echo done` exits with echo's status, so it proves nothing about `check`
    assert not _same_command(COMPILE, WITH_DIR)


def test_other_commands_and_empty_values_do_not_match():
    assert not _same_command(WITH_DIR, "python other.py")
    assert not _same_command("", COMPILE)
    assert not _same_command(WITH_DIR, "")


def test_double_ampersand_redirects_and_quoted_ampersands_are_not_a_clause():
    chained = "python a.py && python b.py"
    assert not _same_command(chained, "python a.py")
    assert not _same_command("python a.py 2>&1", "python a.py")
    assert not _same_command('python -c "print(1 & 2)"', 'python -c "print(1')


def test_a_clean_rerun_of_the_check_clears_the_failure():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "failed", WITH_DIR, result="Command exited with status 1.")
    assert state.check_failing()
    _record(state, "unknown", COMPILE, result="SERVER_PY_OK")
    assert not state.check_failing()


def test_a_rerun_that_fails_again_leaves_it_failing():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "failed", WITH_DIR, result="Command exited with status 1.")
    _record(state, "failed", COMPILE, result="SyntaxError: invalid syntax")
    assert state.check_failing()


def test_a_different_passing_command_does_not_clear_it():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "failed", WITH_DIR, result="Command exited with status 1.")
    _record(state, "unknown", r"cd E:\app && python -m py_compile other.py && echo OK", result="OK")
    assert state.check_failing()


@pytest.mark.parametrize("tail", [
    "python -m pytest tests/test_b.py", "python validate.py", "npm test",
    "dir missing && python -m pytest tests/test_b.py", "dir missing | python validate.py",
    "echo $(python validate.py)", "echo `python validate.py`",
])
def test_a_later_real_or_uncertain_command_is_not_dropped(tail):
    assert not _same_command(COMPILE + " & " + tail, COMPILE)


def test_passing_first_check_does_not_clear_failed_second_check():
    state = TrajectoryState({}, goal="verify both modules", message_count=1)
    first, second = "python -m pytest tests/test_a.py", "python -m pytest tests/test_b.py"
    _record(state, "failed", first + " & " + second, "test_b failed. Command exited with status 1.")
    _record(state, "unknown", first, "1 passed")
    assert state.check_failing()
