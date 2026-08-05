---
name: project-conventions
description: Non-obvious operating conventions for this repo — test isolation, CLI-invocation isolation, and known unresolved decisions. Background knowledge; not a task to run.
user-invocable: false
---

# Project Conventions (agent8088)

Background knowledge for working in this repo. These aren't discoverable from reading the code alone — most come from real incidents.

## Always isolate `HOME` before invoking the CLI

`agent8088` / `python -m agent8088.cli` reads `~/.agent8088/config.txt` and `~/.agent8088/.env` by default. Running it bare, without an isolated environment, can trigger real side effects against the developer's actual config — this happened once: a bare `--help` invocation triggered the one-time `api_key` → `.env` migration against the real `~/.agent8088/config.txt`. It turned out to be lossless (the migration is idempotent and non-destructive by design), but don't rely on that — isolate every time:

```bash
HOME=/tmp/some-sandbox-dir AGENT8088_CONFIG=/nonexistent python -m agent8088.cli ...
```

A `PreToolUse` hook (`.claude/hooks/guard-agent8088-cli.sh`) mechanically blocks bare invocations from the Bash tool as a backstop — but the hook only fires within this session's file-watcher; it does not help in a fresh session until the watcher picks up `.claude/settings.json` (may need `/hooks` to reload, per Claude Code's own caveat about directories not watched at session start).

## Tests must run with `AGENT8088_CONFIG=/nonexistent`

```bash
AGENT8088_CONFIG=/nonexistent .venv/bin/python -m pytest tests/ -q
```

This forces repo-relative path loading so tests never depend on (or write to) a real `config.txt`. Without it, tests can pick up and pollute the real config in exactly the way described above.

## Gateway tests need optional extras installed

`slack-bolt`, `slack-sdk`, `httpx`, `discord.py` are the `gateway` extras (`pip install -e ".[gateway]"`), not core dependencies. Without them, the Slack/Discord platform tests fail with `ModuleNotFoundError` at import time rather than skipping cleanly — that's ~19 failures that look like real breakage but are just a missing optional dependency. Always check `pip show slack-bolt` (or just try installing the extras) before treating a wall of gateway-test failures as a regression.

## An unresolved, real precedence disagreement

`tests/test_providers.py::test_configured_api_key_wins_over_adapter_environment_key` is marked `xfail(strict=False)`, not fixed. `_provider_api_key()`'s docstring says ".env file first, then os.environ, then direct api_key" — the test asserts the opposite (a configured `api_key` should win over an env var). This is a genuine product decision, not a stale test — don't silently resolve it by picking a side. If asked to fix it, surface the disagreement and ask which precedence is intended before changing either the code or the test.

## `_wrap_untrusted` has already broken once from a silent duplicate definition

Two functions both named `_wrap_untrusted` coexisted in `engine.py` after two feature branches touched the same file independently — Python kept only the one defined last, and ruff's `F811` did not catch it in this codebase (verified against both an extracted copy and the real committed blob). `scripts/check_duplicate_defs.py` exists specifically because this bug class can't be trusted to a linter here — run it (or expect the `PostToolUse` test hook to eventually catch a resulting test failure) after any change that touches a large shared module like `engine.py` or `cli.py`.

## GitHub Actions is not available on this account

A CI workflow was tried and removed — Actions is blocked by a billing/spending-limit issue on the repo's GitHub account. Don't propose GitHub-Actions-based automation without first confirming billing is resolved; prefer local/free alternatives (hooks, scripts run manually) — see `pr-check` skill for the manual equivalent of what CI would have run.

## Branch protections and push discipline

Never push to `main` or `development` without the user's explicit go-ahead in that chat turn — a `PreToolUse` hook (`.claude/hooks/guard-protected-push.sh`) mechanically blocks it from the Bash tool, but that's a backstop, not a substitute for asking first. Always open a PR and let the user merge, per their established preference in this repo.
