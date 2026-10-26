"""A check that failed because of how it was written is settled by the edited command running clean.

Seen in a real run: the check ended `node --check server.py 2>&1 | head -1`. `head` does not exist on Windows (exit 255) and
`server.py` is Python. The model fixed the tail (`python -c "import ast; ..."`) and the edited command ran clean, but it was
not the same command and printed no counted summary, so the run still ended "NOT VERIFIED: the latest check failed" over a
final answer saying the check passed."""
from agent8088.trajectory import TrajectoryState, _revised_command

WS = "C:\\w\\proj"
FAILED = (f'cd "{WS}" && echo "=== FILES ===" && dir /b && echo "=== JS SYNTAX CHECK ===" && node --check app.js '
          '&& echo "app.js OK" && node --check server.py 2>&1 | head -1')
FIXED = (f'cd "{WS}" && echo "=== FILES ===" && dir /b && echo "=== JS SYNTAX CHECK ===" && node --check app.js '
         '&& echo "app.js OK" && echo "=== PY SYNTAX CHECK ===" && python -c "import ast; ast.parse(open(\'server.py\').read())"')


def _run(state, command, result, verdict="unknown"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


def _failed_state():
    state = TrajectoryState({}, goal="g", message_count=1)
    _run(state, FAILED, "'head' is not recognized as an internal or external command\nCommand exited with status 255.",
         verdict="failed")
    assert state.check_failing()
    return state


def test_the_edited_check_running_clean_settles_the_failure():
    state = _failed_state()
    _run(state, FIXED, "app.js OK\nserver.py OK")
    assert not state.check_failing()


def test_the_edited_check_failing_again_keeps_the_failure():
    state = _failed_state()
    _run(state, FIXED, "SyntaxError: invalid syntax\nCommand exited with status 1.", verdict="failed")
    assert state.check_failing()


def test_an_unrelated_check_does_not_settle_it():
    state = _failed_state()
    _run(state, "npm run lint", "done")
    assert not _revised_command(FAILED, "npm run lint")
    assert state.check_failing()


def test_a_listing_is_not_a_check():
    state = _failed_state()
    _run(state, f'cd "{WS}" && dir /b', "app.js\nserver.py")
    assert state.check_failing()


def test_an_edited_command_that_prints_a_traceback_keeps_the_failure():
    state = _failed_state()
    _run(state, FIXED, "Traceback (most recent call last):\n  File x\nValueError: bad")
    assert state.check_failing()


def test_a_short_command_is_not_matched_loosely():
    assert not _revised_command("pytest", "pytest -k other")


def test_dropping_the_failed_clause_is_not_an_edit_of_it():
    assert not _revised_command("pytest tests/test_a.py & pytest tests/test_b.py", "pytest tests/test_a.py")
