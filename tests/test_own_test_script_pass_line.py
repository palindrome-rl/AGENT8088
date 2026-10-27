"""A test script the run wrote for itself counts as a passing test when it ends on its own clear pass line.

Seen in a real run: `node battle_test.js` played a full battle to the end with no exception and printed `TEST PASSED`, but that is not a
runner summary with counts, so the result read as unknown and the run ended "Verification: exercised ... no test run passed" over a test
that passed. Only the run's own written file, a pass line that names tests or checks, and an output with no failure words count."""
import subprocess
import sys

import pytest

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


@pytest.mark.parametrize("command", [
    'echo "node battle_test.js"',
    'echo "node battle_test.js" && echo TEST PASSED',
    'python -c "print(\'node battle_test.js\')"',
    'node other/battle_test.js',
    'node battle_test.js || echo TEST PASSED',
    'node battle_test.js & echo TEST PASSED',
    'node battle_test.js ; echo TEST PASSED',
    'node battle_test.js | echo TEST PASSED',
    'node battle_test.js && echo TEST PASSED',
    'node battle_test.js --help',
    'node battle_test.js --list-tests',
    'node --check battle_test.js',
    'node battle_test.js # ignored',
])
def test_ambiguous_invocations_never_receive_pass_credit(command):
    assert not _passes("TEST PASSED", command=command)
    state = _state()
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, "TEST PASSED", failed=False, blocked=False,
                     mutated=False, verdict="unknown", command=command)
    assert operation["verdict"] == "unknown"
    assert state.data["passed_checks"] == 0


@pytest.mark.parametrize("output", [
    'TEST PASSED: false', 'TEST PASSED: no', 'TEST PASSED?', 'TEST PASSED (not run)',
    'tests passed: 0', 'Tests: OK 0', '0 specs passed', '0 assertions succeeded',
    '[agent8088] Error: assertion failed\nTEST PASSED',
    '<<<Error: assertion failed>>>\nTEST PASSED',
    'TEST PASSED\nCommand exited with status 1.',
    'TEST PASSED\nCommand exited with status -1.',
    'TEST PASSED\ntimed out', 'TEST PASSED\nCancelled', '0 errors\nTEST PASSED',
    'TEST PASSED\nnot verified', 'TEST PASSED\nTests skipped',
])
def test_negative_zero_count_and_failure_outputs_stay_unverified(output):
    assert not _passes(output)


def test_a_changed_directory_must_still_resolve_to_the_written_script():
    assert not _own_script_passed('cd C:/elsewhere && node battle_test.js', 'TEST PASSED', WRITTEN, WS)
    assert not _own_script_passed('node Battle_test.js', 'TEST PASSED', WRITTEN, WS)
    assert _own_script_passed('cd /d "C:/w/proj" && node battle_test.js', 'TEST PASSED', WRITTEN, WS)
    assert _own_script_passed('node "C:/w/proj/battle_test.js"', 'TEST PASSED', WRITTEN, WS)
    assert _own_script_passed('node ./battle_test.js', 'TEST PASSED', WRITTEN, WS)


@pytest.mark.parametrize('command, path', [
    ('python -u verify.py', 'verify.py'), ('ruby verify.rb', 'verify.rb'),
    ('bash verify.sh', 'verify.sh'), ('deno run verify.ts', 'verify.ts'),
    ('go run verify.go', 'verify.go'), ('java Verify.java', 'Verify.java'),
    ('pwsh -NoProfile -File verify.ps1', 'verify.ps1'),
    ('powershell -NonInteractive -File verify.ps1', 'verify.ps1'),
])
def test_literal_script_invocations_work_across_common_runtimes(command, path):
    assert _own_script_passed(command, 'All tests passed.', [f'{WS}/{path}'], WS)


def test_workspace_is_forwarded_by_the_trajectory():
    state = TrajectoryState({}, goal="g", message_count=1, workspace=WS)
    state.data['written_paths'] = WRITTEN
    op = state.before_tool('execute_shell')
    state.after_tool(op, 'TEST PASSED', failed=False, blocked=False, mutated=False,
                     verdict='unknown', command='node other/battle_test.js')
    assert op['verdict'] == 'unknown'


@pytest.mark.parametrize('failed, blocked', [(True, False), (False, True)])
def test_tool_failures_and_blocks_are_not_promoted(failed, blocked):
    state = _state()
    op = state.before_tool('execute_shell')
    state.after_tool(op, 'TEST PASSED', failed=failed, blocked=blocked, mutated=False,
                     verdict='unknown', command='node battle_test.js')
    assert op['verdict'] == 'unknown'


def test_real_script_runs_get_credit_but_a_masked_exit_does_not(tmp_path):
    target = tmp_path / 'verify.py'
    target.write_text("print('TEST PASSED')\n", encoding='utf-8')
    command = '"' + sys.executable + '" verify.py'
    good = subprocess.run([sys.executable, 'verify.py'], cwd=tmp_path, capture_output=True,
                          text=True, timeout=10, check=False)
    assert good.returncode == 0
    assert _own_script_passed(command, good.stdout + good.stderr, [str(target)], str(tmp_path))
    target.write_text('raise SystemExit(1)\n', encoding='utf-8')
    masked_command = command + ' || echo TEST PASSED'
    masked = subprocess.run(masked_command, shell=True, cwd=tmp_path, capture_output=True,
                            text=True, timeout=10, check=False)
    assert masked.returncode == 0 and 'TEST PASSED' in masked.stdout
    assert not _own_script_passed(masked_command, masked.stdout + masked.stderr, [str(target)], str(tmp_path))


def test_a_genuine_pass_marks_the_written_work_tested_until_the_next_edit():
    state = TrajectoryState({}, goal='build a script', message_count=1, workspace=WS)
    path = WRITTEN[0]
    def write():
        op = state.before_tool('write_file')
        state.after_tool(op, 'written', failed=False, blocked=False, mutated=True, path=path)
    write()
    op = state.before_tool('execute_shell')
    state.after_tool(op, WRAP_HEAD + 'TEST PASSED' + WRAP_TAIL, failed=False, blocked=False,
                     mutated=False, command='node battle_test.js')
    assert op['verdict'] == 'passed'
    assert state.data['verification'] == 'verified'
    assert state.assurance() == 'tested'
    write()
    assert state.assurance() != 'tested'
