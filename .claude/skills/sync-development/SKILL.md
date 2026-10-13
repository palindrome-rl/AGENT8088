---
name: sync-development
description: Use when the branch-sync guard blocks an edit, when starting a feature or fix in this repository, or when merging origin/development into a branch hits conflicts. Covers how to build on the latest development, how to handle a branch that already has an open PR, and when to merge another PR deliberately.
---

# Building on the latest `development`

Work in this repository is always built on top of the current
`origin/development`. A `PreToolUse` hook blocks the first file edit on a
branch that has fallen behind.

## The normal case

```bash
python3 scripts/sync_development.py --apply
```

That fetches `origin/development` and merges it into the branch you are on.
If the branch has no commits of its own, the merge fast-forwards — the same
result as branching fresh off `development`.

To check without changing anything (exit code 2 means stale):

```bash
python3 scripts/sync_development.py --check
```

## Starting new work

```bash
git fetch origin development
git checkout -b feat/my-thing origin/development
```

## A branch that already has an open PR

Merge, do not rebase. The PR's commits are published and other people may
have pulled them; rebasing rewrites that history and forces everyone else to
recover. `--apply` already does the right thing.

## Building on someone else's unmerged PR

Only when the work genuinely depends on it, and only when a human has named
the PR:

```bash
python3 scripts/sync_development.py --apply --depends-on 253
```

This fetches PR #253 into a local `sync/pr-253` branch and merges it after
`development`. Note that the result is **not** mergeable on its own: your PR
will contain #253's commits until #253 lands. Say so in the PR description.

Never merge open PRs speculatively. There are usually several in flight and
merging them all produces a branch nobody can review.

## When the merge conflicts

`--apply` aborts a conflicting merge and leaves the branch untouched, so you
are never stranded mid-merge. Redo it by hand and resolve:

```bash
git merge origin/development
# resolve the conflicts
git add <files>
git commit --no-edit
```

Resolve in favour of `development` for files you did not touch in this
branch. For files you did touch, read both sides — a conflict usually means
someone changed the thing you are changing, and that is worth knowing before
you continue.

## When the guard is wrong

Deliberate work on an older base (reproducing a bug on a past commit,
preparing a hotfix off a release branch):

```bash
AGENT8088_SKIP_SYNC=1 <command>
```

The guard also stands down on its own when HEAD is detached, when you are on
`development`/`main`/`staging`, and when `origin/development` cannot be
reached — it reports that it could not check rather than blocking.
