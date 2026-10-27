"""A test script the run wrote for itself counts as a passing test when it ends on its own clear pass line.

Seen in a real run: `node battle_test.js` played a full battle to the end with no exception and printed `TEST PASSED`, but that is not a
runner summary with counts, so the result read as unknown and the run ended "Verification: exercised ... no test run passed" over a test
that passed. Only the run's own written file, a pass line that names tests or checks, and an output with no failure words count."""
from agent8088.trajectory import TrajectoryState, _own_script_passed

WS = "C:\\w\\proj"
WRITTEN = [f"{WS}\\battle_test.js", f"{WS}\\app.js"]
WRAP_HEAD = '<<<EXTERNAL_UNTRUSTED_CONTENT source="shell command: node battle_test.js">>>\n'
WRAP_TAIL = "\n<<<END_UNTRUSTED_CONTENT>>>"


def _passes(output, command="node battle_test.js", written=WRITTEN):
    return _own_script_passed(command, WRAP_HEAD + output + WRAP_TAIL, written)


def test_the_real_battle_test_output_counts():
    assert _passes("turn 1: Garchomp used Earthquake\nturn 2: tev1 chose Protect\nplayer won in 14 turns\nTEST PASSED")


def test_other_clear_pass_lines_count():
    for line in ("All tests passed", "all checks passed.", "Tests: OK", "e2e test passed", "assertions succeeded"):
        assert _passes("setup done\n" + line), line


def test_a_script_the_run_did_not_write_does_not_count():
    assert not _passes("TEST PASSED", command="node other_test.js")
    assert not _passes("TEST PASSED", written=[f"{WS}\\app.js"])


def test_an_echo_is_not_a_test_run():
    assert not _passes("TEST PASSED", command='echo "TEST PASSED"')


def test_any_failure_word_in_the_output_keeps_it_unknown():
    assert not _passes("check 1 failed\nTEST PASSED")
    assert not _passes("Error: unexpected token\nTEST PASSED")
    assert not _passes("Traceback (most recent call last):\nTEST PASSED")


def test_a_zero_count_or_an_unrelated_line_does_not_count():
    assert not _passes("0 tests passed")
    assert not _passes("build done\nALL DATA OK")
    assert not _passes("")


def test_only_the_end_of_the_output_counts():
    assert not _passes("TEST PASSED\nstep 2\nstep 3\nstep 4\nsaved file")


def _state():
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = list(WRITTEN)
    return state


def test_the_trajectory_records_it_as_a_passed_check():
    state = _state()
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, WRAP_HEAD + "player won\nTEST PASSED" + WRAP_TAIL, failed=False, blocked=False,
                     mutated=False, verdict="unknown", command=f'cd "{WS}" && node battle_test.js')
    assert operation["verdict"] == "passed"
    assert state.data["passed_checks"] == 1


def test_a_failed_run_is_still_failed():
    state = _state()
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, "TEST PASSED\nCommand exited with status 1.", failed=False, blocked=False,
                     mutated=False, verdict="failed", command="node battle_test.js")
    assert operation["verdict"] == "failed"
    assert state.check_failing()
