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

# Live testing

Testing a feature or branch through the real agent8088 CLI — not just unit
tests — follows the recipe in
[`.claude/skills/agent8088-live-testing/SKILL.md`](.claude/skills/agent8088-live-testing/SKILL.md).
Read it before the first live run. Claude Code loads it as a skill; Codex and
OpenCode read the file directly.

It matters because a careless live run damages real state:

- Isolate the runtime. Set `AGENT8088_HOME` and `AGENT8088_CONFIG` to a
  scratch directory and write a fresh `config.txt` and `.env` there. Never
  read or write the developer's own `~/.agent8088/.env` or `config.txt`.
- Isolate the branch. `git worktree add --detach <scratch>/audit origin/<branch>`
  rather than checking out a branch another session may be using.
- Confirm the model actually dispatched the tool before reporting a feature
  broken. A model declining to call a tool that is in its own tool list is a
  tool-calling reliability gap, not a bug in the feature.
- Clean up with exactly-scoped patterns
  (`pkill -f '<venv-path>/bin/agent8088'`), never a broad `chrome`/`chromium`
  match that could close the developer's own windows.

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

