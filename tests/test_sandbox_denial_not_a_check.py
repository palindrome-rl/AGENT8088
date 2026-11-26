"""A command the sandbox refused to let write did not test anything, so it is not the latest check failing.

Seen in a benchmark run: the model's own scratch check ran `rm -rf mt_test && mkdir -p mt_test/invoices && cp ...`
and the sandbox answered "mkdir: can't create directory 'mt_test/': Read-only file system". That became "the latest
check failed" and the run ended NOT VERIFIED, although the work passed every grader check.
"""
import pytest

from agent8088.trajectory import TrajectoryState

PROBE = ("cd /tmp && rm -rf mt_test && mkdir -p mt_test/invoices && cp /mnt/e/w/monthly_totals.py mt_test/ && "
         "echo '=== run from /tmp ===' && python3 mt_test/monthly_totals.py && cat mt_test/monthly_totals.csv")


def _record(state, verdict, command, result):
    op = state.before_tool("execute_shell")
    state.after_tool(op, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


@pytest.mark.parametrize("result", [
    "mkdir: can't create directory 'mt_test/': Read-only file system\nCommand exited with status 1.",
    "OSError: [Errno 30] Read-only file system: 'out.csv'\nCommand exited with status 1.",
])
def test_a_read_only_refusal_is_not_a_failed_check(result):
    state = TrajectoryState({}, goal="Write monthly_totals.py and run it.", message_count=1)
    _record(state, "failed", PROBE, result)
    assert not state.check_failing()


def test_an_earlier_real_failure_stays_failing():
    state = TrajectoryState({}, goal="Write monthly_totals.py and run it.", message_count=1)
    _record(state, "failed", "python -m pytest -q", "1 failed, 3 passed\nCommand exited with status 1.")
    _record(state, "failed", PROBE, "mkdir: can't create directory 'mt_test/': Read-only file system")
    assert state.check_failing()


def test_an_ordinary_failure_still_counts():
    state = TrajectoryState({}, goal="Write monthly_totals.py and run it.", message_count=1)
    _record(state, "failed", "python -m pytest -q", "1 failed, 3 passed\nCommand exited with status 1.")
    assert state.check_failing()
