"""Read-only git inspection runs on the host, because the sandbox is not the repo.

The sandbox mounts `artifacts/` as its workspace — writes are confined there on
purpose. `git_status`, `git_diff` and `git_log` ran inside it, so they were
looking at a directory that is not a git repository and returned
`fatal: not a git repository`. Their whole purpose is reporting on the real
checkout, so they now run on the host like the mutating git tools already did.

What makes that safe is that all three are FIXED commands: no `args=`, so no
model-controlled text reaches the shell. The mutating tools take arguments and
are gated separately.
"""
import pytest

READ_ONLY_GIT = ("git_status", "git_diff", "git_log")
MUTATING_GIT = ("git_clone", "git_commit", "git_push", "git_create_pr")


@pytest.mark.parametrize("name", READ_ONLY_GIT)
def test_read_only_git_runs_on_the_host(engine, name):
    assert engine.TOOL_SPECS[name].get("host"), f"{name} must not run in the sandbox"


@pytest.mark.parametrize("name", READ_ONLY_GIT)
def test_read_only_git_takes_no_arguments(engine, name):
    """The safety case for running these outside the sandbox."""
    assert not engine.TOOL_SPECS[name].get("args"), (
        f"{name} gained an argument; host execution now has a model-controlled "
        "input and needs re-justifying")


@pytest.mark.parametrize("name", MUTATING_GIT)
def test_mutating_git_still_runs_on_the_host(engine, name):
    assert engine.TOOL_SPECS[name].get("host"), f"{name} lost its host flag"


def test_execute_shell_stays_sandboxed(engine):
    """The general escape hatch must not follow git onto the host."""
    assert not engine.TOOL_SPECS["execute_shell"].get("host")


def test_run_sandboxed_stays_sandboxed(engine):
    assert not engine.TOOL_SPECS["run_sandboxed"].get("host")


def test_git_status_reports_the_real_repository(engine):
    """End to end: the failure this fixes was `fatal: not a git repository`."""
    engine.PERMISSION_MODE = "edit"
    result = engine.run_tool("git_status", {})
    assert "not a git repository" not in result
    assert "##" in result, f"expected a branch line, got: {result[:120]!r}"


def test_readonly_mode_still_allows_git_status(engine):
    """Host execution must not cost the tool its read-only availability."""
    engine.PERMISSION_MODE = "readonly"
    assert engine.check_permission(
        "shell", command="git status --short --branch", host=True) is True


def test_an_arbitrary_host_read_is_still_refused_in_readonly(engine):
    """The exemption is for fixed tool commands, not for host reads in general."""
    assert engine.check_permission(
        "shell", command="cat /etc/passwd", host=True) is False
    assert engine.check_permission(
        "shell", command="git show HEAD:.env", host=True) is False


def test_a_git_command_with_arguments_is_not_exempt(engine):
    """Only the verbatim, argument-free tool command qualifies."""
    assert engine._is_fixed_host_tool_command("git status --short --branch") is True
    assert engine._is_fixed_host_tool_command("git status --short --branch -- .env") is False
    assert engine._is_fixed_host_tool_command("git diff -- secrets.txt") is False


def test_the_exemption_follows_the_registry(engine):
    """A tool that gains an argument must drop out by construction."""
    engine.TOOL_SPECS["git_status"] = dict(engine.TOOL_SPECS["git_status"],
                                           args=["path"])
    assert engine._is_fixed_host_tool_command("git status --short --branch") is False


def test_destructive_git_is_still_hard_blocked(engine):
    """The always-on floor is unaffected by where these run."""
    assert engine._hard_blocked_shell("git push origin main")
    assert engine._hard_blocked_shell("git reset --hard")
