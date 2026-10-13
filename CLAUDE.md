# Commit messages

Do not append a `Co-Authored-By: Claude ...` trailer to commit messages in this repo. Commit as the human author only.

# Branch workflow

Every feature and fix is built on top of the current `origin/development`.

Before your first file edit:

```bash
python3 scripts/sync_development.py --check    # exit 2 means the branch is stale
python3 scripts/sync_development.py --apply    # fetch + merge origin/development
```

- Starting new work: `git checkout -b <type>/<name> origin/development`.
- The branch already has an open PR: `--apply` merges `origin/development`
  into it. Merge, never rebase — the PR's commits are published.
- Other people's open PRs are never merged automatically. Build on one only
  when a human names it: `--apply --depends-on <number>`.
- A conflicting merge is aborted and the branch left unchanged; resolve by
  hand with `git merge origin/development`.
- Deliberate work on an older base: set `AGENT8088_SKIP_SYNC=1`.

Claude Code enforces this through a `PreToolUse` hook in
`.claude/settings.json`, which blocks the first edit on a stale branch. Codex
and OpenCode have no equivalent hook, so run the script yourself before
editing.

# Development memory

Use TencentDB Agent Memory only as optional development context for this repository.

- Proxy: `http://127.0.0.2:8096`
- Team: `team-example`
- Shared agent: `Agent8088 Developer` (`agent-example`)
- Search the shared Wiki and CodeGraph for relevant architecture, decisions, conventions, and code relationships before implementation.
- Current code, tests, and explicit user requirements are authoritative; verify shared memory because it may be stale.
- Configure access with each developer's personal key outside the repository. Never commit keys, tokens, passwords, `.env` values, production data, customer data, or personal data.
- This integration is development-only. Do not make application/runtime code depend on the memory server, send production traffic to it, or alter production deployment behavior.
- Use only team-shared development assets; do not expose private or personal memories.
- If the LAN server is unavailable, continue without shared context and report it.

