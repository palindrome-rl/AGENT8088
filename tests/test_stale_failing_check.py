"""A failing check settles when the file that failed has run cleanly since, and a file run in the wrong place is not a failed check.

Seen in real runs: a script crashed once, was fixed, and ran fine under a different command line (extra `cd`, `&&`, an echo of
its exit code), but the run still ended "NOT VERIFIED: the latest check failed". Another run started a browser file with
Node (`node js\\main.js`: `document is not defined`) and was marked failing for good."""
import pytest

from agent8088.trajectory import TrajectoryState, _command_core, _wrong_place_failure

WS = "C:\\w\\proj"


def _state(written=("watch.py", "main.js", "app.mjs")):
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = [f"{WS}\\{name}" for name in written]
    return state


def _run(state, command, result, verdict="unknown"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


TRACE = "Traceback (most recent call last):\n  File \"watch.py\", line 211, in <module>\nKeyError: 'x'\nCommand exited with status 1."


def test_a_written_script_that_crashed_and_later_ran_clean_is_settled():
    state = _state()
    _run(state, f'cd "{WS}" && python watch.py', TRACE, verdict="failed")
    assert state.check_failing() and state.data["failing_files"] == ["watch.py"]
    _run(state, f'cd /d "{WS}\\outputs" && python watch.py && echo "--- second ---" && python watch.py', "Checked 4 sites")
    assert not state.check_failing()


def test_a_clean_run_that_still_prints_a_traceback_does_not_settle_it():
    state = _state()
    _run(state, f"python watch.py", TRACE, verdict="failed")
    _run(state, f"python watch.py 2>&1", "step 1 ok\nTraceback (most recent call last):\n  File x\nValueError: bad")
    assert state.check_failing()


def test_running_some_other_file_does_not_settle_it():
    state = _state()
    _run(state, "python watch.py", TRACE, verdict="failed")
    _run(state, "python other.py", "ok")
    _run(state, "dir", "listing")
    assert state.check_failing()


def test_a_test_runner_failure_is_not_settled_by_running_a_script():
    state = _state()
    _run(state, "python -m unittest discover -s tests", "FAILED (failures=1)", verdict="failed")
    assert state.check_failing() and not state.data["failing_files"]
    _run(state, "python watch.py", "ok")
    assert state.check_failing()
    _run(state, "python -m unittest discover -s tests", "Ran 3 tests\n\nOK", verdict="passed")
    assert not state.check_failing()


@pytest.mark.parametrize("result", [
    "ReferenceError: document is not defined\n    at main.js:3:1\nCommand exited with status 1.",
    "ReferenceError: window is not defined\nCommand exited with status 1.",
    "SyntaxError: Cannot use import statement outside a module\nCommand exited with status 1.",
    "SyntaxError: Unexpected token 'export'\nCommand exited with status 1.",
    "Error [ERR_REQUIRE_ESM]: require() of ES Module app.mjs not supported\nCommand exited with status 1.",
    "NameError: name 'document' is not defined\nCommand exited with status 1.",
])
def test_a_browser_file_started_outside_a_browser_is_not_a_failed_check(result):
    state = _state()
    _run(state, f'cd "{WS}" && node main.js 2>&1', result, verdict="failed")
    assert not state.check_failing()


def test_a_real_error_in_a_written_script_still_fails():
    state = _state()
    _run(state, "node main.js", "ReferenceError: score is not defined\nCommand exited with status 1.", verdict="failed")
    assert state.check_failing()


def test_a_check_script_that_cannot_find_document_is_still_a_failure():
    """A script named like a check is the run's own check: it failing says the check does not work."""
    state = _state(written=("verify.js",))
    _run(state, "node verify.js", "ReferenceError: document is not defined\nCommand exited with status 1.", verdict="failed")
    assert state.check_failing()


def test_the_wrong_place_rule_needs_a_written_file():
    assert not _wrong_place_failure("node other.js", "ReferenceError: document is not defined", [f"{WS}\\main.js"])
    assert _wrong_place_failure("node main.js", "ReferenceError: document is not defined", [f"{WS}\\main.js"])


def test_the_failing_check_names_the_command_not_the_folder():
    state = _state()
    long_cd = "E:\\Palindrome Labs\\harness_eval\\20261011-champions-doubles-battle-simulator\\runs\\x\\workspace"
    _run(state, f'cd /d "{long_cd}" && python watch.py', TRACE, verdict="failed")
    assert state.failing_check_detail().startswith("python watch.py ->")
    assert state.data["failing_command"].startswith("cd /d")


@pytest.mark.parametrize("command, core", [
    ('cd /d "C:\\a b\\c" && node x.js 2>&1', "node x.js 2>&1"),
    ("cd proj && cd sub && pytest -q", "pytest -q"),
    ("pushd 'C:\\a' ; python a.py", "python a.py"),
    ("cd proj", "cd proj"),
    ("python a.py", "python a.py"),
])
def test_command_core(command, core):
    assert _command_core(command) == core
