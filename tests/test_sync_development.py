"""Tests for scripts/sync_development.py (the branch-sync guard)."""

from __future__ import annotations

import importlib.util
import sys
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync_development.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_development", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_development"] = module
    spec.loader.exec_module(module)
    return module


sync = _load_module()


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def remote_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """A bare 'origin' with a development branch, plus a clone of it."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "development")
    git(seed, "config", "user.email", "t@example.com")
    git(seed, "config", "user.name", "Test")
    (seed / "README.md").write_text("base\n")
    git(seed, "add", "README.md")
    git(seed, "commit", "-qm", "base")
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    git(clone, "config", "user.email", "t@example.com")
    git(clone, "config", "user.name", "Test")
    return origin, clone


def advance_development(origin: Path, tmp_path: Path, n: int = 1) -> None:
    """Push n new commits onto origin/development."""
    work = tmp_path / "pusher"
    if not work.exists():
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
        git(work, "config", "user.email", "t@example.com")
        git(work, "config", "user.name", "Test")
    git(work, "checkout", "-q", "development")
    git(work, "pull", "-q", "--ff-only")
    for i in range(n):
        existing = (work / "upstream.txt").read_text() if (work / "upstream.txt").exists() else ""
        (work / "upstream.txt").write_text(f"{existing}line {i}\n")
        git(work, "add", "upstream.txt")
        git(work, "commit", "-qm", f"upstream {i}")
    git(work, "push", "-q", "origin", "development")


# --- current_branch -------------------------------------------------------


def test_current_branch_reports_the_checked_out_branch(remote_and_clone):
    _, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    assert sync.current_branch(clone) == "feat/x"


def test_current_branch_is_none_on_detached_head(remote_and_clone):
    _, clone = remote_and_clone
    git(clone, "checkout", "-q", "--detach", "HEAD")
    assert sync.current_branch(clone) is None


# --- check ----------------------------------------------------------------


def test_check_reports_synced_when_branch_contains_development(remote_and_clone):
    _, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    status = sync.check(clone, fetch=False)
    assert status.state == "synced"
    assert status.behind == 0


def test_check_reports_behind_when_development_moved_ahead(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    advance_development(origin, tmp_path, n=2)
    git(clone, "fetch", "-q", "origin")

    status = sync.check(clone, fetch=False)
    assert status.state == "behind"
    assert status.behind == 2
    assert "git merge origin/development" in "\n".join(status.fix_commands)


def test_check_on_the_base_branch_itself_is_not_blocking(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    advance_development(origin, tmp_path, n=1)
    git(clone, "fetch", "-q", "origin")

    status = sync.check(clone, fetch=False)
    assert status.state == "base"
    assert status.blocking is False
    assert "git pull --ff-only" in "\n".join(status.fix_commands)


def test_check_is_unknown_when_base_branch_is_missing(tmp_path):
    repo = tmp_path / "solo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feat/x")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "a")

    status = sync.check(repo, fetch=False)
    assert status.state == "unknown"
    assert status.blocking is False


def test_check_is_unknown_on_detached_head(remote_and_clone):
    _, clone = remote_and_clone
    git(clone, "checkout", "-q", "--detach", "HEAD")
    status = sync.check(clone, fetch=False)
    assert status.state == "unknown"
    assert status.blocking is False


def test_behind_status_is_blocking(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    advance_development(origin, tmp_path, n=1)
    git(clone, "fetch", "-q", "origin")
    assert sync.check(clone, fetch=False).blocking is True


# --- apply ----------------------------------------------------------------


def test_apply_merges_development_into_the_feature_branch(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    (clone / "mine.txt").write_text("mine\n")
    git(clone, "add", "mine.txt")
    git(clone, "commit", "-qm", "my work")
    advance_development(origin, tmp_path, n=1)

    result = sync.apply(clone)

    assert result.ok is True
    assert (clone / "upstream.txt").exists(), "development's commit should now be present"
    assert (clone / "mine.txt").exists(), "own work must be preserved"
    assert sync.check(clone, fetch=False).state == "synced"


def test_apply_fast_forwards_a_branch_with_no_commits_of_its_own(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/fresh")
    advance_development(origin, tmp_path, n=1)

    result = sync.apply(clone)

    assert result.ok is True
    assert (clone / "upstream.txt").exists()


def test_apply_reports_failure_and_aborts_on_conflict(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    (clone / "upstream.txt").write_text("conflicting\n")
    git(clone, "add", "upstream.txt")
    git(clone, "commit", "-qm", "conflicting change")
    advance_development(origin, tmp_path, n=1)

    result = sync.apply(clone)

    assert result.ok is False
    assert "conflict" in result.message.lower()
    assert not (clone / ".git" / "MERGE_HEAD").exists(), "merge should be aborted, not left mid-flight"


def test_apply_refuses_when_the_working_tree_is_dirty(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    advance_development(origin, tmp_path, n=1)
    (clone / "README.md").write_text("uncommitted edit\n")

    result = sync.apply(clone)

    assert result.ok is False
    assert "uncommitted" in result.message.lower()
    assert (clone / "README.md").read_text() == "uncommitted edit\n"


def test_apply_on_the_base_branch_fast_forwards_it(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    advance_development(origin, tmp_path, n=1)

    result = sync.apply(clone)

    assert result.ok is True
    assert (clone / "upstream.txt").exists()


# --- hook decision --------------------------------------------------------


def test_hook_denies_the_edit_when_the_branch_is_behind(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    advance_development(origin, tmp_path, n=3)
    git(clone, "fetch", "-q", "origin")

    payload = sync.hook_decision(sync.check(clone, fetch=False), env={})

    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert "3 commit" in reason
    assert "sync_development.py --apply" in reason
    assert "AGENT8088_SKIP_SYNC" in reason


def test_hook_allows_the_edit_when_the_branch_is_synced(remote_and_clone):
    _, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    payload = sync.hook_decision(sync.check(clone, fetch=False), env={})
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_hook_allows_the_edit_when_the_override_is_set(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    advance_development(origin, tmp_path, n=1)
    git(clone, "fetch", "-q", "origin")

    payload = sync.hook_decision(
        sync.check(clone, fetch=False), env={"AGENT8088_SKIP_SYNC": "1"}
    )
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_hook_allows_the_edit_when_status_cannot_be_determined(tmp_path):
    repo = tmp_path / "solo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feat/x")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "a")

    payload = sync.hook_decision(sync.check(repo, fetch=False), env={})
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"


# --- open PR lookup -------------------------------------------------------


def test_open_pr_for_branch_returns_the_pr_number(monkeypatch):
    def fake_gh(args, cwd):
        assert "pr" in args and "list" in args
        return [{"number": 261, "title": "Report SearXNG failures"}]

    monkeypatch.setattr(sync, "_gh_json", fake_gh)
    assert sync.open_pr_for_branch("fix/qa-pr-search", Path(".")) == 261


def test_open_pr_for_branch_returns_none_when_gh_is_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(sync, "_gh_json", boom)
    assert sync.open_pr_for_branch("feat/x", Path(".")) is None


# --- cli ------------------------------------------------------------------


def test_cli_check_exits_nonzero_when_behind(remote_and_clone, tmp_path):
    origin, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    advance_development(origin, tmp_path, n=1)
    git(clone, "fetch", "-q", "origin")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--no-fetch", "--repo", str(clone)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "behind" in proc.stdout.lower()


def test_cli_check_exits_zero_when_synced(remote_and_clone):
    _, clone = remote_and_clone
    git(clone, "checkout", "-q", "-b", "feat/x")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--no-fetch", "--repo", str(clone)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
