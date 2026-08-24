---
name: github-pr-workflow
description: Take a change from working tree to an opened pull request using the git_* tools, in the right order.
version: 1.0.0
category: software-development
---

Use this whenever the user asks for a PR, or to push/commit/open a change.
Seven tools exist for this: git_status, git_diff, git_log, git_clone,
git_commit, git_push, git_create_pr. This skill is the order to call them in.

## The order

1. `git_status` — see what's actually changed before doing anything. Never
   commit blind.
2. `git_diff` — read the real diff. If something unexpected is staged (a
   secret, an unrelated file), stop and say so instead of committing it.
3. `git_commit` — only after status/diff confirm the change is what's
   intended. Message follows the repo's own convention if one exists
   (Agent8088 itself uses `feat:`/`fix:`/`docs:`/`test:`/`refactor:` — check
   `git_log` for the pattern if unsure).
4. `git_push` — only when the user asked to push. Never push as a side effect
   of being asked to commit.
5. `git_create_pr` — only when the user asked for a PR, and only after the
   push it depends on has actually happened.

## Rules that apply regardless of what the user asked

- **Never push directly to a protected branch** (`main`, `master`,
  `development` — whatever the repo calls its main line). If the current
  branch is one of those, say so and ask before pushing, don't silently
  create a feature branch to route around it.
- **A user approving one push does not mean future pushes are pre-approved.**
  Confirm scope per request, not per session.
- **`git_log`** is for establishing convention (commit message style, whether
  this repo branches from `main` or `development`) before acting, not just for
  answering "what happened recently."
- If `git_status` shows unstaged changes the user didn't mention, ask before
  including them in a commit — don't assume "commit my change" means "commit
  everything sitting in the tree."
