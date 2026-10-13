---
name: promote-to-staging
description: Use when promoting merged `development` work onto the `staging-1.2` release branch — "move the latest commits to staging", "sync staging with development", "cut a staging release" — or when a merge into staging has clobbered a staging-only setting (memory_engine, the sandbox comment block, .gitignore, the installer branch).
---

# Promoting `development` to `staging-1.2`

## Overview

`staging-1.2` is a long-lived release branch, not a branch re-cut per release.
It carries the same source as `development` but a **trimmed, release-configured
tree**: no Python test suite, no agent config, no handoff notes, and a handful
of settings deliberately different from `development`.

Promotion is one merge commit with `staging-1.2` as the first parent. The merge
itself is easy; the job is making sure the staging-only divergences survive it.
Git's auto-merge will silently take `development`'s side on several of them.

**The rule: after the merge, every staging-only value must still read as
staging's, and no development-only path may be tracked.**

## When to use

- Promoting the PRs merged into `development` since the last promotion
- A staging merge finished but `memory_engine`, `.gitignore`, or the sandbox
  comment block look like `development`'s
- Deciding whether a new development-only file belongs on staging

Not for merging `development` into a feature branch — that is
[`sync-development`](../sync-development/SKILL.md).

## The pattern

Read it off the history rather than trusting this file:

```bash
git log origin/staging-1.2 --pretty='%h|%an|%p|%s' | grep -i 'into staging-1.2'
```

Every entry is the same shape:

| | |
|---|---|
| Direction | `development` merged **into** `staging-1.2` |
| Parents | first = staging tip, second = development tip |
| Method | `git merge --no-ff` — never rebase, squash, or cherry-pick |
| Message | `Merge development PRs <N>-<M> into staging-1.2; preserve staging release settings` |
| Fallback message | `Merge latest development into staging-1.2; preserve staging release settings` when the PR range is not clean |

Historically these were pushed straight to `staging-1.2` with no PR. Open a PR
only when asked; say which you did.

## Steps

### 1. Establish the range

```bash
git fetch origin staging-1.2 development
git log --oneline origin/staging-1.2..origin/development \
  | grep -o 'Merge pull request #[0-9]*' | grep -o '[0-9]*' | sort -n
```

The lowest and highest PR numbers are `<N>` and `<M>` in the commit message.
They should continue straight on from the last promotion's range.

### 2. Derive the real divergence — do not guess

A plain `git diff origin/development origin/staging-1.2` is **misleading**: it
mixes deliberate divergence with staging merely being behind. Diff the staging
tip against the development commit it last merged (its second parent):

```bash
STAGING_TIP=$(git rev-parse origin/staging-1.2)
LAST_DEV=$(git rev-parse "$STAGING_TIP^2")
git diff --name-status "$LAST_DEV" "$STAGING_TIP" | grep -v $'^D\ttests/'
```

That list — and only that list — is what staging deliberately keeps different.
Anything else takes `development`'s version, including new source modules.

### 3. Merge in an isolated worktree

Never promote from the shared checkout; other sessions use it.

```bash
git worktree add --detach "$SCRATCH/promote" origin/staging-1.2
cd "$SCRATCH/promote"
git checkout -B promote/staging-1.2-<N>-<M>
git merge --no-ff --no-commit origin/development
```

### 4. Resolve

**The test suite.** Every `tests/` path conflicts as modify/delete. Staging
ships none of it:

```bash
git rm -r -q --cached tests >/dev/null 2>&1; rm -rf tests
```

**Development-only paths.** Staging is a release branch: agent config, dev
scripts, and working notes do not ship. Remove any that the merge introduced:

```bash
for p in .claude scripts/sync_development.py \
         AUDIT-GAPS-REVIEW.md HANDOFF-*.md LOCAL-INTEGRATION-HANDOFF.md QA-COMPLETION-*.md; do
  git rm -r -q --cached "$p" >/dev/null 2>&1; rm -rf "$p"
done
```

**`AGENTS.md` and `CLAUDE.md`.** Keep staging's versions. They describe the
development harness — the sync guard, the live-testing skill, `tests/` — none of
which staging ships:

```bash
git checkout "$STAGING_TIP" -- AGENTS.md CLAUDE.md
```

**Real content conflicts.** Only a few, usually wiki pages and `README.md`.
Read both sides; neither branch is automatically newer. Staging's wiki is
maintained on staging via `scripts/sync_wiki.py`, so staging sometimes has
newer prose than `development` for the same section, while `development` has
the newer settings tables. Take whichever actually reflects shipped behaviour,
and say which in the commit or PR.

### 5. Restore what auto-merge clobbered

These merge cleanly and wrongly — git takes `development`'s side without
reporting a conflict. Check and restore every one:

| File | Staging value |
|---|---|
| `src/agent8088/config.txt` | `memory_engine=native` (development: `mem0`) |
| `src/agent8088/config.txt` | the one-line `# sandbox_allowed_domains=api.example.com`, not development's expanded block |
| `.gitignore` | `.claude/` — not development's `.claude/*` plus `!.claude/settings.json` allowlist |
| `.gitignore` | keeps `/tests/` and the tool-cache / editor / env entries |

Note `config.txt` at the repo root is **not** staging-divergent; only
`src/agent8088/config.txt` is. Do not "fix" the root one.

### 6. Verify before committing

```bash
# staging-only settings
grep -n '^memory_engine' src/agent8088/config.txt          # native
grep -n 'sandbox_allowed_domains' src/agent8088/config.txt # one-line form
grep -n '^\.claude/$\|^/tests/' .gitignore
grep -n 'UPDATE_BRANCH' src/agent8088/cli.py               # "staging-1.2"
grep -n 'SOURCE_BRANCH' scripts/sync_wiki.py               # "staging-1.2"
grep -n 'AGENT8088_BRANCH:-staging-1.2' install.sh
git ls-files assets | grep demo                            # demo.gif + demo.mp4

# nothing development-only leaked in
git ls-files tests | wc -l                                 # 0
git ls-files .claude | wc -l                               # 0

# every known divergence still differs from development
git diff --quiet origin/development -- install.sh || echo "ok: install.sh divergent"
```

Then compare the whole picture against the previous promotion. The divergence
list should match step 2's list, plus any new strip entries you added:

```bash
git diff --name-status origin/development HEAD | grep -v $'^D\ttests/'
```

### 7. Commit

```bash
git commit -m "Merge development PRs <N>-<M> into staging-1.2; preserve staging release settings"
git log -1 --pretty=%p   # must print two hashes, staging tip first
```

Then confirm the source payload is real — a promotion that only moves docs
means source changes were lost:

```bash
git diff --stat "$STAGING_TIP" HEAD
```

### 8. Push

Stop and ask before pushing. `staging-1.2` is a release branch.

## Deciding about a new development-only file

The strip list is not fixed; `development` grows new files staging has never
seen. Ask what the file is for:

| Kind | Goes to staging? |
|---|---|
| Runtime source (`src/agent8088/**.py`), web UI, shipped skills | **Yes** — take development's |
| Packaging that references shipped source (`pyproject.toml`) | **Yes** |
| User-facing docs (`docs/wiki/`, `README.md`) | **Yes**, minus staging's own edits |
| Test suite, agent config, dev scripts, handoff/audit/QA notes | **No** |
| Docs describing the development harness | **No** |

When unsure, check whether a sibling of the same kind is already stripped — the
stripped set includes `docs/document-workflow.md`, `docs/gateway-audit.md`,
`docs/slash-commands.md`, `docs/webui-test-manual/`, and
`scripts/verify_tool_intelligence.py`.

## Common mistakes

| Mistake | Consequence |
|---|---|
| Diffing `development` against `staging-1.2` to find the divergence | Treats "staging is behind" as deliberate; you strip real new source |
| Trusting a clean auto-merge | `memory_engine` and `.gitignore` silently revert to development's |
| Promoting from the shared checkout | Collides with other sessions on the same repo |
| Rebasing or squashing | Breaks the first-parent chain the next promotion reads to find its baseline |
| `git stash` to park local work | Old stashes exist in this repo; popping applies a foreign entry |
| Assuming `development` is newer for every doc hunk | Staging's wiki is maintained separately and is sometimes ahead |
| Pushing without asking | `staging-1.2` is what `agent8088 --update` installs from |
