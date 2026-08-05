---
name: pr-check
description: Pre-PR verification checklist for this repo — dual-suite test run, duplicate-definition scan, and a baseline-vs-branch comparison so pre-existing failures never get mistaken for regressions. Invoke before opening or merging a PR.
disable-model-invocation: true
---

# PR Check

Run this before opening a PR, and again before merging one, against whichever branch you're about to merge into (usually `development`).

**Never skip step 2.** This repo has had pre-existing test failures at multiple points; the only way to tell "this PR broke something" from "this was already broken" is to run the target branch's baseline first, in the same session, right before comparing.

## 1. Sync and check for merge conflicts

```bash
git fetch origin
git merge-tree --write-tree origin/<target-branch> <your-branch>
```

A non-empty conflict section means real conflicts to resolve before continuing — don't proceed to step 2 with unresolved conflicts.

## 2. Establish the target branch's baseline

Run the exact same commands on the target branch as you will on your branch, in the same environment, before comparing:

```bash
git worktree add --detach /tmp/pr-check-baseline origin/<target-branch>
cd /tmp/pr-check-baseline
AGENT8088_CONFIG=/nonexistent <repo>/.venv/bin/python -m pytest tests/ -q
cd -
git worktree remove --force /tmp/pr-check-baseline
```

Note which tests fail on the baseline, if any — these are pre-existing, not yours to fix as part of this PR unless you're deliberately fixing them (and if so, say so explicitly in the PR description).

## 3. Run your branch's full suite

```bash
AGENT8088_CONFIG=/nonexistent .venv/bin/python -m pytest tests/ -v
```

Compare against the baseline from step 2. Any failure that wasn't in the baseline is a regression — stop and fix it before continuing. A failure fixed relative to the baseline is worth calling out positively in the PR description.

## 4. Run the duplicate-definition check

```bash
.venv/bin/python scripts/check_duplicate_defs.py
```

This exists because ruff's F811 has already missed a real duplicate-function bug in this exact codebase (`_wrap_untrusted`, fixed in PR #13) — don't skip it just because a linter would normally catch this class of bug elsewhere.

## 5. Run the functional verification suite, isolated

```bash
VERIFY_HOME="$(mktemp -d)"
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" .venv/bin/python scripts/verify_features.py
rm -rf -- "$VERIFY_HOME"
```

Exit code is non-zero on any real failure. `⊘ SKIP` entries for missing optional deps (`playwright`) or unset API keys (Tavily/Exa) are expected and not failures — don't chase those down as part of a routine PR check.

**If the gateway extras aren't installed** (`pip show slack-bolt` fails), install them first — `pip install -e ".[gateway]"` — or the gateway platform tests will report hard import failures that look like real breakage but are just a missing optional dependency:

```bash
.venv/bin/pip install -q 'slack-bolt>=1.18.0,<2' 'slack-sdk>=3.23.0,<4' 'httpx>=0.24.0' 'discord.py>=2.3.0,<3'
```

## 6. Report

State plainly: pre-existing failures (unchanged from baseline), new failures (must fix before merge), and fixed failures (call out as a positive in the PR description). Don't silently pick a side on any test whose *expectation*, not just its pass/fail state, changed relative to the baseline — if the code and the test disagree about intended behavior, that's a product decision, not something to resolve by editing whichever one is more convenient.
