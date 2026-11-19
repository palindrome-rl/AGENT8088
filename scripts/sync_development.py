#!/usr/bin/env python3
"""Keep the current branch built on top of the latest `development`.

Every agent working in this repository runs this before touching files:

    python3 scripts/sync_development.py --check    # exit 2 if the branch is stale
    python3 scripts/sync_development.py --apply    # fetch + merge origin/development

It only ever merges `origin/development` into the branch you are on. It does
not pull in other people's open pull requests; pass `--depends-on <number>` to
build on one deliberately.

Claude Code wires this up as a hook (see .claude/settings.json); Codex and
OpenCode run it from the command line, as CLAUDE.md and AGENTS.md describe.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE = os.environ.get("AGENT8088_SYNC_BASE", "development")
DEFAULT_REMOTE = os.environ.get("AGENT8088_SYNC_REMOTE", "origin")
SKIP_ENV = "AGENT8088_SKIP_SYNC"

# Branches that are themselves integration targets: never block work on them.
PROTECTED = {"main", "master", "development", "staging", "staging-1.3", "AGENT8088-v1.3"}


# --- process helpers ------------------------------------------------------


def _env() -> dict[str, str]:
    """Environment that keeps git from reaching for a terminal.

    git and ssh will happily grab /dev/tty for a prompt even when stdin is
    redirected, which hangs an agent session with no visible cause.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["SSH_ASKPASS"] = "echo"
    env["GIT_SSH_COMMAND"] = env.get(
        "GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new"
    )
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def run_git(cwd: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _git_out(cwd: Path, *args: str) -> str | None:
    proc = run_git(cwd, *args)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _gh_json(args: list[str], cwd: Path):
    proc = subprocess.run(
        ["gh", *args],
        cwd=str(cwd),
        env=_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout or "null")


# --- repository queries ---------------------------------------------------


def current_branch(cwd: Path) -> str | None:
    """The checked-out branch, or None when HEAD is detached."""
    name = _git_out(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    return name or None


def commits_behind(cwd: Path, base_ref: str) -> int | None:
    count = _git_out(cwd, "rev-list", "--count", f"HEAD..{base_ref}")
    return int(count) if count and count.isdigit() else None


def open_pr_for_branch(branch: str, cwd: Path) -> int | None:
    """The number of this branch's own open PR, or None. Best effort."""
    try:
        data = _gh_json(
            ["pr", "list", "--head", branch, "--state", "open", "--json", "number"],
            cwd,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not data:
        return None
    return data[0].get("number")


def is_dirty(cwd: Path) -> bool:
    return bool(_git_out(cwd, "status", "--porcelain"))


# --- status ---------------------------------------------------------------


@dataclass
class SyncStatus:
    state: str  # synced | behind | base | unknown
    branch: str | None
    base: str
    base_ref: str
    behind: int = 0
    reason: str = ""
    fix_commands: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.state == "behind"

    def summary(self) -> str:
        if self.state == "synced":
            return f"[sync] {self.branch} is up to date with {self.base_ref}."
        if self.state == "behind":
            plural = "" if self.behind == 1 else "s"
            return (
                f"[sync] {self.branch} is {self.behind} commit{plural} behind "
                f"{self.base_ref}. Merge it before making changes."
            )
        if self.state == "base":
            if self.behind:
                return f"[sync] {self.branch} is {self.behind} behind {self.base_ref}; pull before branching."
            return f"[sync] on {self.branch}, up to date. Create a feature branch for new work."
        return f"[sync] could not determine branch status: {self.reason}"


def check(
    cwd: Path,
    base: str = DEFAULT_BASE,
    remote: str = DEFAULT_REMOTE,
    fetch: bool = True,
) -> SyncStatus:
    base_ref = f"{remote}/{base}"
    cwd = Path(cwd)

    if _git_out(cwd, "rev-parse", "--git-dir") is None:
        return SyncStatus("unknown", None, base, base_ref, reason="not a git repository")

    branch = current_branch(cwd)
    if branch is None:
        return SyncStatus("unknown", None, base, base_ref, reason="detached HEAD")

    if fetch:
        run_git(cwd, "fetch", "--quiet", remote, base)

    if _git_out(cwd, "rev-parse", "--verify", "--quiet", base_ref) is None:
        return SyncStatus(
            "unknown", branch, base, base_ref, reason=f"{base_ref} not found (offline?)"
        )

    behind = commits_behind(cwd, base_ref)
    if behind is None:
        return SyncStatus(
            "unknown", branch, base, base_ref, reason=f"could not compare against {base_ref}"
        )

    if branch in PROTECTED:
        return SyncStatus(
            "base",
            branch,
            base,
            base_ref,
            behind=behind,
            fix_commands=[f"git pull --ff-only {remote} {branch}"],
        )

    if behind == 0:
        return SyncStatus("synced", branch, base, base_ref)

    return SyncStatus(
        "behind",
        branch,
        base,
        base_ref,
        behind=behind,
        fix_commands=[
            "python3 scripts/sync_development.py --apply",
            f"# or by hand:  git fetch {remote} && git merge {base_ref}",
        ],
    )


# --- apply ----------------------------------------------------------------


@dataclass
class SyncResult:
    ok: bool
    message: str


def apply(
    cwd: Path,
    base: str = DEFAULT_BASE,
    remote: str = DEFAULT_REMOTE,
    depends_on: int | None = None,
) -> SyncResult:
    cwd = Path(cwd)
    base_ref = f"{remote}/{base}"

    if is_dirty(cwd):
        return SyncResult(
            False,
            "Working tree has uncommitted changes. Commit or set them aside first, "
            "then re-run --apply.",
        )

    branch = current_branch(cwd)
    if branch is None:
        return SyncResult(False, "HEAD is detached; check out a branch first.")

    fetched = run_git(cwd, "fetch", "--quiet", remote, base)
    if fetched.returncode != 0:
        return SyncResult(False, f"Could not fetch {base_ref}: {fetched.stderr.strip()}")

    if branch in PROTECTED:
        proc = run_git(cwd, "merge", "--ff-only", base_ref)
        if proc.returncode != 0:
            return SyncResult(
                False,
                f"{branch} has diverged from {base_ref} and cannot fast-forward: "
                f"{proc.stderr.strip()}",
            )
        return SyncResult(True, f"{branch} fast-forwarded to {base_ref}.")

    merged = _merge(cwd, base_ref)
    if not merged.ok:
        return merged

    if depends_on is not None:
        dep = _merge_pr(cwd, depends_on)
        if not dep.ok:
            return dep
        return SyncResult(True, f"{merged.message} {dep.message}")

    return merged


def _merge(cwd: Path, ref: str) -> SyncResult:
    proc = run_git(cwd, "merge", "--no-edit", ref)
    if proc.returncode == 0:
        return SyncResult(True, f"Merged {ref}.")
    run_git(cwd, "merge", "--abort")
    return SyncResult(
        False,
        f"Merging {ref} hit a conflict; the merge was aborted and your branch is "
        "unchanged. Re-run `git merge " + ref + "` and resolve it by hand.",
    )


def _merge_pr(cwd: Path, number: int) -> SyncResult:
    local = f"sync/pr-{number}"
    proc = subprocess.run(
        ["gh", "pr", "checkout", str(number), "--branch", local, "--detach"],
        cwd=str(cwd),
        env=_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return SyncResult(False, f"Could not fetch PR #{number}: {proc.stderr.strip()}")
    fetch = run_git(cwd, "fetch", DEFAULT_REMOTE, f"pull/{number}/head:{local}", "--force")
    if fetch.returncode != 0:
        return SyncResult(False, f"Could not fetch PR #{number}: {fetch.stderr.strip()}")
    return _merge(cwd, local)


# --- hook interface -------------------------------------------------------


def hook_decision(status: SyncStatus, env: dict[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    allow = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": status.summary(),
        }
    }
    if env.get(SKIP_ENV):
        return allow
    if not status.blocking:
        return allow

    plural = "" if status.behind == 1 else "s"
    reason = (
        f"Branch `{status.branch}` is {status.behind} commit{plural} behind "
        f"`{status.base_ref}`. This repository requires work to be built on the "
        f"latest {status.base}.\n\n"
        "Run this first, then retry the edit:\n"
        "    python3 scripts/sync_development.py --apply\n\n"
        f"It fetches and merges {status.base_ref} into your branch (and updates the "
        "branch's own open PR). It will not merge anyone else's PR; pass "
        "--depends-on <number> if this work genuinely builds on one.\n\n"
        f"To work on a stale base on purpose, set {SKIP_ENV}=1."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def session_context(status: SyncStatus, cwd: Path) -> dict:
    lines = [status.summary()]
    if status.state in {"behind", "synced"} and status.branch:
        pr = open_pr_for_branch(status.branch, cwd)
        if pr:
            lines.append(f"[sync] this branch has open PR #{pr}.")
    if status.blocking:
        lines.append("[sync] run `python3 scripts/sync_development.py --apply` before editing files.")
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }


# --- cli ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report status; exit 2 if stale")
    mode.add_argument("--apply", action="store_true", help="fetch and merge the base branch")
    mode.add_argument("--hook", choices=["pre-tool-use", "session-start"], help="emit hook JSON")
    parser.add_argument("--repo", default=".", help="repository directory (default: cwd)")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--no-fetch", action="store_true", help="trust the cached remote refs")
    parser.add_argument("--depends-on", type=int, help="also merge this open PR locally")
    args = parser.parse_args(argv)

    cwd = Path(args.repo).resolve()

    if args.apply:
        result = apply(cwd, base=args.base, remote=args.remote, depends_on=args.depends_on)
        print(result.message)
        return 0 if result.ok else 1

    if args.hook:
        # Hooks must never break the session: any failure is reported as "allow".
        try:
            status = check(cwd, base=args.base, remote=args.remote, fetch=not args.no_fetch)
            payload = (
                session_context(status, cwd)
                if args.hook == "session-start"
                else hook_decision(status)
            )
        except Exception as exc:  # noqa: BLE001 - a guard must not block work
            payload = {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart"
                    if args.hook == "session-start"
                    else "PreToolUse",
                    "additionalContext"
                    if args.hook == "session-start"
                    else "permissionDecisionReason": f"[sync] check skipped: {exc}",
                }
            }
            if args.hook == "pre-tool-use":
                payload["hookSpecificOutput"]["permissionDecision"] = "allow"
        print(json.dumps(payload))
        return 0

    status = check(cwd, base=args.base, remote=args.remote, fetch=not args.no_fetch)
    print(status.summary())
    for line in status.fix_commands:
        print(f"    {line}")
    return 2 if status.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
