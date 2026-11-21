"""A check that failed and was then rerun with only output thrown away or leftovers removed first counts as rerun.

Seen in a benchmark run (http-api-server): the agent's own test script failed, the agent fixed the server and ran it
again as `cd /d "<ws>" && del server.log 2>nul && python run_e2e.py`, which printed "all 32 checks" passed. The `2>nul`
and the `del` made the rerun unreadable to the matcher, "the latest check failed" stayed, and a run the grader passed
ended NOT VERIFIED. Deleting code, tests or data first still does not count."""
import pytest

from agent8088.trajectory import TrajectoryState, _revised_command

WS = r'cd /d "E:\bench runs\http-api-server_2\workspace\artifacts"'
FAILED = f"{WS} && python run_e2e.py"
FAILED_OUTPUT = ("http.client.RemoteDisconnected: Remote end closed connection without response\n"
                 "Command exited with status 1.")
PASSED_OUTPUT = "=== Phase 3: PORT env var honoured ===\n[PASS] server stopped (final) -- exit code=1\n\nRESULT: all 32 checks"


@pytest.mark.parametrize("rerun", [
    f"{WS} && del server.log 2>nul && python run_e2e.py",
    f"{WS} && del /f /q server.log server.pid && python run_e2e.py",
    f"{WS} && python run_e2e.py 2>nul",
    f"{WS} && rm -f out.log && python run_e2e.py >/dev/null",
    f"{WS} && erase run.lock && python run_e2e.py",
])
def test_a_rerun_after_clearing_leftovers_is_the_same_check(rerun):
    assert _revised_command(FAILED, rerun)


@pytest.mark.parametrize("rerun", [
    f"{WS} && del test_server.py && python run_e2e.py",          # removes code
    f"{WS} && del notes.json && python run_e2e.py",              # removes data the check may depend on
    f"{WS} && del *.log && python run_e2e.py",                   # a pattern, not named leftovers
    f"{WS} && del server.log && python run_e2e.py --quick",      # a different check
    f"{WS} && python run_e2e.py 2>errors.txt",                   # a real redirection is still unreadable
    f"{WS} && python other_e2e.py",
])
def test_other_reruns_still_do_not_settle_the_failure(rerun):
    assert not _revised_command(FAILED, rerun)


def _shell(state, command, result, *, failed=False, verdict="unknown"):
    operation = state.before_tool("execute_shell")
    state.after_tool(operation, result, failed=failed, blocked=False, mutated=False, verdict=verdict, command=command)


def test_the_run_from_the_benchmark_is_settled_by_its_rerun():
    state = TrajectoryState({}, goal="g", message_count=1)
    _shell(state, FAILED, FAILED_OUTPUT, failed=True, verdict="failed")
    assert state.check_failing()
    _shell(state, f"{WS} && del server.log 2>nul && python run_e2e.py", PASSED_OUTPUT)
    assert not state.check_failing()


def test_deleting_the_test_first_leaves_the_failure_standing():
    state = TrajectoryState({}, goal="g", message_count=1)
    _shell(state, FAILED, FAILED_OUTPUT, failed=True, verdict="failed")
    _shell(state, f"{WS} && del test_server.py && python run_e2e.py", PASSED_OUTPUT)
    assert state.check_failing()
