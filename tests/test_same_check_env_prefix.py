"""A check that failed with a setting in front of it is settled by the plain command passing.

Seen in a benchmark run: the model reproduced a broken build with `BUILD_FAST=1 python build.py`, fixed it, and ran
the full `python build.py`, which passed. The run still ended "the latest check failed", citing the fast build.
The plain command is the fuller check, so its pass settles the failure. The other way round does not count: a
setting such as SKIP_TESTS=1 can make a check pass without fixing anything.
"""
from agent8088.trajectory import _same_command


def test_the_plain_command_settles_a_failure_run_with_a_setting():
    assert _same_command("BUILD_FAST=1 python build.py", "python build.py")
    assert _same_command("A=1 B=two python -m pytest -q", "python -m pytest -q")


def test_a_setting_added_to_the_rerun_does_not_settle_it():
    assert not _same_command("python build.py", "BUILD_FAST=1 python build.py")
    assert not _same_command("python -m pytest", "SKIP_SLOW=1 python -m pytest")


def test_a_different_command_after_the_setting_does_not_match():
    assert not _same_command("BUILD_FAST=1 python build.py", "python other.py")
    assert not _same_command("BUILD_FAST=1 python build.py", "")


def _record(state, verdict, command, result="x"):
    op = state.before_tool("execute_shell")
    state.after_tool(op, result, failed=False, blocked=False, mutated=False, verdict=verdict, command=command)


def test_the_full_build_passing_clears_the_fast_builds_failure():
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="Fix the build.", message_count=1)
    _record(state, "failed", "BUILD_FAST=1 python build.py",
            result="link error: module_077 uses parse_heder\nCommand exited with status 1.")
    assert state.check_failing()
    _record(state, "unknown", "python build.py", result="build ok: 120 modules, 121 symbols")
    assert not state.check_failing()
