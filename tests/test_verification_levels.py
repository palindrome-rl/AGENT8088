"""How much the run's evidence supports "done" is a level, not a yes or no; a test run counts however the model wrote the
command around it; and a file copy is not a check."""
import pytest

from agent8088 import trajectory
from agent8088.trajectory import TrajectoryState, _is_check_run


def _state(**data):
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data.update(data)
    return state


def _run(state, command, result, verdict="unknown", mutated=False, failed=False):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=failed, blocked=False, mutated=mutated, verdict=verdict,
                     command=command)


# -- which commands count as a test run ------------------------------------------------------------

@pytest.mark.parametrize("command", [
    'cd /d "E:\\a b" && python -m unittest discover -s tests -v 2>&1',
    "python -m pytest -q 2>&1 | tail -20",
    "cd x && npm test 2>&1",
    "del _e.py && python -m unittest discover -s tests 2>&1 | tail -5",
    "node tests.js",
    "cd x && node test_engine.js 2>&1",
    "python tests/test_api.py",
    'cd x && "E:\\My Tools\\.venv\\Scripts\\python.exe" -m unittest discover -s tests -v 2>&1',
    'python -c "print(1); print(2)" 2>&1 && python -m unittest discover -s tests 2>&1',
    "cd x & findstr /n a b.py & python -m unittest discover -s tests 2>&1",
    'cd x && node --check app.js && echo "app.js syntax" && node smoke_test.js 2>&1',
    "echo === engine tests === && node test/engine.test.js",
    "python _verify_run.py 2>&1",
    "pytest -q > nul 2>&1",
])
def test_a_test_run_is_recognised_whatever_is_around_it(engine, command):
    assert engine._runs_a_test_runner(command)


@pytest.mark.parametrize("command", [
    "python server.py",
    'python -m unittest 2>&1 && echo "5 tests passed"',
    'echo "5 passed"',
    'printf "Ran 3 tests" && pytest',
    "python app.py 2>&1 | tee out.txt",
    "node tests.js > out.txt",
    'python -c "print(1)"',
    "python run_all.py 2>&1",
    "type results.txt",
    "",
])
def test_a_command_that_is_not_a_test_run_is_not_one(engine, command):
    assert not engine._runs_a_test_runner(command)


def test_separators_inside_quotes_do_not_split_a_command(engine):
    assert engine._split_command('python -c "a(); b()" && python -m unittest 2>&1') == [
        'python -c "a(); b()" ', ' python -m unittest 2>&1']
    assert engine._split_command("cd x & dir & pytest") == ["cd x ", " dir ", " pytest"]
    assert engine._split_command("pytest 2>&1") == ["pytest 2>&1"]


def test_only_a_real_runner_verdict_counts_so_a_printed_pass_does_not(engine):
    args = {"command": 'cd x && python -m unittest discover -s tests 2>&1'}
    real = "Ran 15 tests in 0.001s\n\nOK"
    assert engine._evidence_status("execute_shell", args, real, "passed", "summary line") == "passed"
    fake = {"command": 'echo "15 passed"'}
    assert engine._evidence_status("execute_shell", fake, "15 passed", "passed", "summary line") == "unknown"


# -- what is a check, for the failing-check flag ---------------------------------------------------

@pytest.mark.parametrize("command", [
    'xcopy "C:\\w\\proj\\tests" "C:\\w\\out" /E /I /Y && rmdir /s /q "C:\\w\\out\\tests\\__pycache__"',
    "robocopy src build /E",
    "cd tests && dir",
    "rm -rf build/ && ls tests",
    "move build old_build",
])
def test_moving_or_listing_files_is_not_a_check_even_when_a_folder_is_called_tests(command):
    assert not _is_check_run(command)


@pytest.mark.parametrize("command", [
    "pytest tests/",
    "cd proj && python -m unittest discover -s tests",
    "bash scripts/check.sh",
    "npm run build",
    "cd x && xcopy a b && pytest",
    "somefancytool tests",
    "",
])
def test_everything_else_keeps_the_plain_word_match(command):
    assert _is_check_run(command)


def test_a_failed_copy_does_not_leave_the_run_marked_as_failing_a_check():
    state = _state()
    _run(state, 'xcopy "C:\\w\\tests" "C:\\w\\out" /E && rmdir "C:\\w\\out\\tests\\__pycache__"',
         "The system cannot find the file specified.\nCommand exited with status 1.", verdict="failed", failed=False)
    assert not state.check_failing()


def test_a_failed_test_run_still_does():
    state = _state()
    _run(state, "python -m unittest discover -s tests 2>&1", "FAILED (failures=1)", verdict="failed")
    assert state.check_failing()


# -- the levels --------------------------------------------------------------------------------------

def test_nothing_changed_means_no_level():
    assert _state().assurance() == ""


def test_changed_and_never_run_is_unchecked():
    state = _state()
    _run(state, "", "wrote file", mutated=True)
    assert state.assurance() == "unchecked"


def test_a_passing_test_run_after_the_change_is_tested():
    state = _state()
    _run(state, "", "wrote file", mutated=True)
    _run(state, "python -m unittest discover -s tests 2>&1", "Ran 3 tests\n\nOK", verdict="passed")
    assert state.assurance() == "tested" and state.data["assurance"] == "tested"


def test_a_clean_run_of_the_program_is_exercised_not_tested():
    state = _state()
    _run(state, "", "wrote file", mutated=True)
    state.note_ran_ok()
    assert state.assurance() == "exercised"


def test_a_change_after_the_clean_run_drops_back():
    state = _state()
    _run(state, "", "wrote file", mutated=True)
    state.note_ran_ok()
    _run(state, "", "wrote file", mutated=True)
    assert state.assurance() == "unchecked"


def test_a_page_that_loaded_cleanly_is_exercised():
    state = _state(mutation_count=1, smoke_notes=["index.html: smoke test passed"])
    assert state.assurance() == "exercised"


def test_reading_the_files_back_is_inspected():
    state = _state(mutation_count=1, verification="inspected")
    assert state.assurance() == "inspected"


def test_a_failing_check_or_page_is_failed_whatever_else_passed():
    assert _state(mutation_count=1, check_failing=True, verification="verified").assurance() == "failed"
    assert _state(mutation_count=1, smoke_notes=["index.html: smoke test FAILED - x"]).assurance() == "failed"


def test_the_answer_says_which_level_it_reached(engine):
    tested = _state(mutation_count=1, verification="verified")
    assert "Verification: tested." in engine._with_task_notes("Done.", tested)
    exercised = _state(mutation_count=1, smoke_notes=["index.html: smoke test passed"])
    answer = engine._with_task_notes("Done.", exercised)
    assert "Verification: exercised." in answer and "weaker than a passing test" in answer
    assert not answer.startswith("NOT VERIFIED")


def test_no_level_line_when_the_work_failed_or_nothing_changed(engine):
    assert "Verification:" not in engine._with_task_notes("Done.", _state())
    failed = engine._with_task_notes("Done.", _state(mutation_count=1, check_failing=True, failing_check="x -> y"))
    assert failed.startswith("NOT VERIFIED") and "Verification: tested" not in failed
