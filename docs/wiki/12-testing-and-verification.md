# Testing & Verification

[← Wiki index](README.md)

Three layers, all runnable offline with no model backend.

| Layer | Command | Scale |
|---|---|---|
| Unit tests | `pytest tests/` | 604 tests, ~10s |
| Feature verification | `scripts/verify_features.py` | 89 checks, 13 sections |
| Exhaustive verification | `scripts/verify_everything.py` | 450 checks, 20 sections |

## 1. Unit tests

```sh
AGENT8088_CONFIG=/nonexistent python -m pytest tests/ -q
```

**`AGENT8088_CONFIG=/nonexistent` is not optional.** It forces packaged-default
loading so tests never read — or write — your real `config.txt`. Without it a
test run can pick up and mutate your live configuration.

Per area:

```sh
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_permission.py -q      # permission modes + floors
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_security_fixes.py -q  # shell/git hard blocks
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_ssrf.py -q            # SSRF
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_egress.py -q          # allowed_domains / blocked_domains
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_exfil_guard.py -q     # outbound secret refusal
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_turn_budget.py -q     # token / cost / wall-clock ceilings
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_command_allowlist.py -q  # allow_commands + write blast radius
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_audit_log.py -q       # audit trail
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_capabilities.py -q    # capability self-report
AGENT8088_CONFIG=/nonexistent python -m pytest tests/gateway/test_rate_limit.py -q  # gateway rate limiting
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_mcp.py -q             # MCP client
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_mcp_server.py -q      # MCP server
AGENT8088_CONFIG=/nonexistent python -m pytest tests/gateway/ -q                # all 3 platforms
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_env_key_store.py -q   # .env store + redaction
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_providers.py -q       # providers + key precedence
AGENT8088_CONFIG=/nonexistent python -m pytest tests/test_subagents.py -q       # sub-agents + guardrails
```

### Gateway extras are required

Without them, the Slack/Discord tests fail at **import** rather than skipping —
roughly 19 failures that look like real breakage but are a missing optional
dependency:

```sh
pip install -e ".[gateway]"
```

## 2. Feature verification

```sh
VERIFY_HOME="$(mktemp -d)"
trap 'rm -rf -- "$VERIFY_HOME"' EXIT
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  python scripts/verify_features.py
```

Runs real behaviour — git operations in temp repos, a real browser if available,
real sandbox execution. Covers core loading, sub-agents, sandboxing, browser,
SSRF, git, cron, providers, images, skills, persona, guardrails and search.

**Anything unavailable is reported as `⊘ SKIP` with the reason, never a silent
pass.** Exit code is non-zero on any real failure.

## 3. Exhaustive verification

```sh
VERIFY_HOME="$(mktemp -d)"
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  python scripts/verify_everything.py
rm -rf -- "$VERIFY_HOME"
```

20 sections including per-tool spec integrity, the full shell-classifier
hard-block matrix, adversarial/edge cases, and the CLI surface.

## 4. Duplicate-definition check

```sh
python scripts/check_duplicate_defs.py
```

Fails if a module defines the same top-level function or class twice. This
exists because **Python silently keeps only the last definition** — no
`SyntaxError`, no import error. It has already bitten this codebase: two
functions named `_wrap_untrusted` coexisted in `engine.py` after two branches
touched the same file, and ruff's `F811` did **not** flag it here (verified
against both an extracted copy and the committed blob). A 40-line AST check
catches it with certainty where a linter heuristic didn't.

## Isolation rules for anything you write

Non-negotiable, because violating them has caused a real incident in this repo:

1. **Never invoke the CLI without an isolated `HOME`.** A bare
   `agent8088 --help` triggers the one-time `.env` key migration against your
   real `~/.agent8088/config.txt`.

   ```sh
   HOME="$(mktemp -d)" AGENT8088_CONFIG=/nonexistent python -m agent8088.cli --help
   ```

2. **Always set `AGENT8088_CONFIG=/nonexistent`** for tests.
3. **Use `AGENT8088_HOME`** for verification scripts.
4. **Mock `subprocess.run`** rather than executing real mutating commands.

A `PreToolUse` hook in `.claude/hooks/guard-agent8088-cli.sh` blocks bare
invocations as a backstop, but the discipline is the actual protection.

## Pre-PR checklist

The `pr-check` skill (`.claude/skills/pr-check/`) automates this:

1. `git fetch origin` and dry-run the merge for conflicts.
2. **Run the target branch's baseline first**, in a worktree. Without it you
   cannot distinguish "this PR broke something" from "this was already broken."
3. Run your branch's full suite; compare against that baseline.
4. Run the duplicate-def check.
5. Run the functional suite with an isolated `HOME`.
6. Report pre-existing vs new vs fixed failures separately — and never silently
   pick a side on a test whose *expectation* changed.

## Public-release gate

Before publishing, run the strict local gate (there is no hosted CI):

```sh
uv run python scripts/release_check.py
```

It requires a fresh lockfile, all Python tests, a focused lint baseline,
duplicate-definition check, Python and WhatsApp-bridge dependency audits, a
wheel install smoke test, and a real native-sandbox proof. It fails rather than
skipping when native sandbox prerequisites are absent.

Run it after `agent8088 --sandbox-setup` on macOS, Linux, and Windows. Windows
needs the one-time restricted-account setup accepted during that command.

Manual release evidence remains required for WhatsApp, Slack, Discord, and
email: authenticate a staging account, send and receive one authorized message,
confirm an unauthorized sender is refused, and verify disconnect/reconnect.

## Interpreting expected skips

These are normal on a clean machine and not failures:

| Skip | Why |
|---|---|
| `REAL browser page load` | `playwright` not installed |
| `web_search REAL query` | all backends failed, or ddgs rate limited |
| `configured search backend reachable` | no local SearXNG on `127.0.0.1:8888` |
| `REAL native sandbox` | sandbox runtime not installed |

## Current state

Verified on the tree this wiki documents:

| Suite | Result |
|---|---|
| Unit tests | 624 passed, 0 failed |
| `verify_features.py` | 89 passed, 0 failed, 4 skipped |
| `verify_everything.py` | 450 passed, 0 failed, 4 skipped |
| Duplicate-def check | clean |

There is **no CI**. GitHub Actions is blocked by a billing issue on this
account, so a workflow was written and then removed rather than left
permanently red. Everything above runs locally and for free — run it before
opening a PR.
