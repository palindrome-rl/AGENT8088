---
name: agent8088-live-testing
description: Use when asked to test, verify, audit, or "break" an agent8088 feature or branch through the real CLI (not just unit tests) — especially when isolation from the user's real ~/.agent8088 config, or from other in-progress worktrees/branches on the same repo, matters.
---

# Agent8088 Live Testing

## Overview

Driving agent8088's actual CLI end-to-end (real provider, real REPL, real tool calls) catches things unit tests can't — but it's easy to accidentally touch the user's real config, collide with another agent's worktree, or mistake a model refusing to call a tool for the tool being broken. This is the isolation + driving + verification recipe, distilled from a baseline run that hit each of those.

## Isolate the branch

Don't fight over an already-checked-out branch and don't fumble with worktree-switching tools mid-task. Create a **detached-HEAD** worktree from the remote ref, in scratch space — this sidesteps "branch already checked out elsewhere" entirely and needs no cleanup dance:

```bash
git fetch origin <branch>
git worktree add --detach <scratch-dir>/audit origin/<branch>
```

If a `uv`/`pip` venv for the exact same commit already exists in scratch from earlier work, `diff -rq` the two `src/` trees before reusing it — don't assume, verify.

## Isolate the runtime, never the real config

```bash
export AGENT8088_HOME=<scratch-dir>/home
export AGENT8088_CONFIG=$AGENT8088_HOME/config.txt
```

- Write `config.txt` and `.env` (`chmod 600`) fresh in scratch. **Never open** the user's real `~/.agent8088/.env` or `config.txt` — not even read-only "just to check"; treat them as off-limits like any other dotfile.
- The Chromium binary (~250MB) and native-sandbox runtime are large, non-sensitive downloads — reusing them read-only is fine and saves real time. Either copy `~/.agent8088/{runtime,playwright-browsers}` into the scratch home, or point `PLAYWRIGHT_BROWSERS_PATH` at the OS-shared Playwright cache (`~/Library/Caches/ms-playwright` on macOS). Pick one, don't agonize — both are read-only references to generic binaries, not user data. Check `df -h` first; copying can fail loudly on a nearly-full disk.
- Found another session's leftover scratch `.env` with what looks like a real key for an internal/LAN provider? Don't reuse it — you can't verify it's still authorized for you. Build your own.

## Drive the CLI

`_read_line()`/`_permission_choice()` fall back to `console.input()` when stdin isn't a tty, so piped stdin supplies both chat turns and any approval prompts (`o`/`s`/`d`, in order they'd be asked):

```bash
printf '<turn 1>\n<turn 2>\n/quit\n' | \
  AGENT8088_HOME=... AGENT8088_CONFIG=... AGENT8088_PERMISSION=full-auto \
  <venv>/bin/agent8088 > run.log 2>&1
```

`AGENT8088_PERMISSION=full-auto` skips escalation prompts entirely — appropriate for a scratch workspace with nothing sensitive in `allowed_paths`.

## Before concluding anything is broken: confirm the model actually called the tool

A model can flatly refuse a tool that's right there in its own tool list ("I can't access the web") — that's a **model tool-calling reliability gap**, not a broken feature. Grep the log for the actual dispatch line (e.g. `model tool calls (turn N): [...]`) before drawing any conclusion. If it's empty, the test didn't run — it doesn't mean the feature failed. Some providers (e.g. local Ollama) don't use native function-calling at all (`native_tools=False` in `providers.py`) and rely on a weaker prompt-based convention — expect more refusals there than from a cloud/native-tool-calling model.

## Test with real tasks, not just a fetch

A trivial "visit this URL and read the heading" only proves the happy path. Cover, in roughly this order of value: happy path → multi-field form fill (text + radio + checkbox + select + textarea) → structured extraction (multiple items, exact values) → security/edge-case inputs (malformed URL, disallowed scheme, SSRF targets — if the model self-censors before calling the tool, call the underlying function directly to test the guard itself) → forced resource-limit stress test (tighten timeout/step config to force a failure path) → session-state round-trip (save/resume state, verify it actually re-syncs) → a **complex chained task**: extract real data from one page, then use that exact data in a subsequent action on a different page.

**The chained task is where things actually break.** Independently verify the extracted ground truth (a plain `curl`/direct code call) and then check the *raw* final state, not just the agent's own summary of it — a sub-agent can leak its own reasoning into typed field text, self-detect the corruption in one reasoning step, then drop the caveat and confidently report clean success one step later.

## Clean up

- Kill only exactly-scoped processes (`pkill -f '<venv-path>/bin/agent8088'`) — never a broad pattern like `chrome`/`chromium` that could match the user's real, unrelated browser windows.
- Stop anything long-lived you started as a side effect (e.g. `ollama serve`) or say plainly in your report that you left it running.
- Re-check disk space if you copied large caches.

## If a real bug turns up

Fix it with TDD (RED before code, GREEN after) in the isolated worktree — see `test-driven-development`. Never push without explicit confirmation, and don't touch worktrees/branches you didn't create.
