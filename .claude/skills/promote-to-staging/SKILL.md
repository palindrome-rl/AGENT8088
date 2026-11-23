---
name: promote-to-staging
description: Use when promoting merged `development` work to `staging-1.3` or `staging-1.3` to production `AGENT8088-v1.3` — "move the latest commits to staging", "release to production", "cut a staging release" — or when a promotion conflicts or a branch seems to have drifted from the others.
---

# Promoting `development` → `staging-1.3` → `AGENT8088-v1.3`

Since 2026-11-19 the three branches share one history, with production
(`AGENT8088-v1.3`) as the base, and they carry **the same tree**. A promotion is
an ordinary merge commit: nothing is trimmed and no URL is rewritten.

What makes one tree work on every channel:

- **Installers** default to `AGENT8088-v1.3`. Other channels install with
  `AGENT8088_BRANCH=development` or `AGENT8088_BRANCH=staging-1.3`.
- **`agent8088 --update`** follows the branch the install has checked out
  (`_resolve_update_branch` in `cli.py`). It falls back to `UPDATE_BRANCH`
  (production) only when that branch is gone from the remote.
- **Default settings** are the same on every branch, such as
  `memory_engine=native` and temperature 0.8. A default that must differ per
  channel needs a design decision first, not a quiet edit on one branch.

## Promote

1. **Choose the commit.** Pick the source commit that passed its PR checks,
   for example the tip of `origin/development`, and note its full SHA.
2. **Open a PR from the source branch into the target**, with **Create a merge
   commit** as the merge method:

   ```bash
   gh pr create --repo palindrome-rl/AGENT8088 --base staging-1.3 --head development \
     --title "release: promote development to staging-1.3" --body-file PROMOTE_BODY.md
   ```

   For production the base is `AGENT8088-v1.3` and the head is `staging-1.3`.
   Keep the body file outside the repository. Write it with a file tool, not a
   shell heredoc, because backticks in Markdown run as commands in a shell.
3. **Expect no conflicts.** If one appears, something was committed directly to
   the target branch. Stop, find that commit, and bring it into `development`
   first through its own PR. Don't resolve the conflict inside the promotion.
4. **Check the tree.** The target tree after the merge equals the source tree:
   `git diff <source SHA> <merged result>` prints nothing.
5. **Merge, then verify** that the promoted commit is now in the target:

   ```bash
   git fetch origin
   git merge-base --is-ancestor <source SHA> origin/<target> && echo ok
   ```

## Production is a publication

Merging into `AGENT8088-v1.3`:
- changes what the public installers and `agent8088 --update` install
- builds and pushes the GHCR image (`publish-image.yml`)
- syncs the public wiki when `docs/wiki/` changed (`wiki.yml`)

Before asking, run the Tier 1 smoke set on the staging tip and compare it with the
last Tier 1 baseline (see `bench/README.md`, "Tier 1"):

```bash
python bench/run.py --profile <profile> --tier 1 --out tier1-runs
python bench/report.py tier1-runs/results.jsonl --tier 1 --baseline <last tier 1 summary.json>
```

Put the result and its "runs to read" list in the release PR. A failed bar is the
release owner's call, not a silent go: say which rule failed and whether the cause
is the harness, the model, the environment or infra.

Get the release owner's go-ahead first. Afterwards, check that the image and
wiki workflow runs succeeded.

## Rules that keep the branches identical

- **All changes enter through `development`.** Never commit directly to
  `staging-1.3` or `AGENT8088-v1.3`. An urgent production fix goes in as a PR to
  production and is merged back into `development` the same day.
- **Never squash, rebase, cherry-pick or force-push** any of the three branches.
- **Rollback is a revert PR**, never a reset.
