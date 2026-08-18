# Testing & Verification

[← Wiki index](README.md)

Three layers, all runnable offline with no model backend.

| Layer | Command | Covers |
|---|---|---|
| Unit tests | `uv run python -m pytest tests/` | Permission layer, tools, gateway, MCP |
| Feature verification | `scripts/verify_features.py` | Real behaviour in temp repos and sandboxes |
| Exhaustive verification | `scripts/verify_everything.py` | Tool specs, shell-classifier matrix, CLI surface |

## Prerequisites

Install the sandbox runtime before running anything here:

```sh
agent8088 --sandbox-setup     # or: start Docker
```

Agent8088 refuses to run commands with no isolation available — see
[No unsandboxed fallback](06-sandboxing.md#no-unsandboxed-fallback) — so the
checks that exercise shell and permission behaviour cannot complete without it.

## 1. Unit tests

```sh
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/ -q
```

**`AGENT8088_CONFIG=/nonexistent` is not optional.** It forces packaged-default
loading so tests never read — or write — your real `config.txt`. Without it a
test run can pick up and mutate your live configuration.

Per area:

```sh
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_permission.py -q      # permission modes + floors
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_security_fixes.py -q  # shell/git hard blocks
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_ssrf.py -q            # SSRF
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_egress.py -q          # allowed_domains / blocked_domains
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_exfil_guard.py -q     # outbound secret refusal
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_turn_budget.py -q     # token / cost / wall-clock ceilings
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_command_allowlist.py -q  # allow_commands + write blast radius
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_audit_log.py -q       # audit trail
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_capabilities.py -q    # capability self-report
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/gateway/test_rate_limit.py -q  # gateway rate limiting
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_mcp.py -q             # MCP client
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_mcp_server.py -q      # MCP server
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/gateway/ -q                # all 5 platforms
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_env_key_store.py -q   # .env store + redaction
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_providers.py -q       # providers + key precedence
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_subagents.py -q       # sub-agents + guardrails
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_memory.py -q        # memory: store, recall, RRF
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/memory/ -q               # memory: embed, extract, end-to-end, FTS safety
```

### Gateway extras are required

Without them, the Slack/Discord adapters fail at **import** rather than skipping
— which looks like real breakage but is a missing optional dependency:

```sh
uv sync --all-extras --locked
```

## 2. Feature verification

```sh
VERIFY_HOME="$(mktemp -d)"
trap 'rm -rf -- "$VERIFY_HOME"' EXIT
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  uv run python scripts/verify_features.py
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
  uv run python scripts/verify_everything.py
rm -rf -- "$VERIFY_HOME"
```

20 sections including per-tool spec integrity, the full shell-classifier
hard-block matrix, adversarial/edge cases, and the CLI surface.

## 4. Duplicate-definition check

```sh
uv run python scripts/check_duplicate_defs.py
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
   HOME="$(mktemp -d)" AGENT8088_CONFIG=/nonexistent uv run python -m agent8088.cli --help
   ```

2. **Always set `AGENT8088_CONFIG=/nonexistent`** for tests.
3. **Use `AGENT8088_HOME`** for verification scripts.
4. **Mock `subprocess.run`** rather than executing real mutating commands.
5. **Write generated files to `artifacts/`, never the repo root.** Use the
   `artifacts_dir` fixture. A session-scoped guard in `tests/conftest.py`
   fails the run if anything new appears beside `pyproject.toml`, so this one
   enforces itself.

A `PreToolUse` hook in `.claude/hooks/guard-agent8088-cli.sh` blocks bare
invocations as a backstop, but the discipline is the actual protection.

## 5. Live-model tool-choice scoring (opt-in)

The suite can prove a search query leaves with the year attached. It cannot
prove the model decides to search in the first place, or resists searching for
something it already knows — that is judgement, and only a real model has any.

```sh
A8088_LIVE_MODEL=1 AGENT8088_HOME="$(mktemp -d)" \
  uv run --extra dev python scripts/verify_tool_intelligence.py
```

It runs the shared scenario table in `tests/data/tool_intelligence_cases.py`
against the configured provider and scores tool choice, writing a report to
`artifacts/`. It refuses to start without `A8088_LIVE_MODEL=1` and refuses to
run against a real `~/.agent8088`.

Not part of the default suite: it costs tokens and is not deterministic. Set
the pass threshold with `A8088_TOOL_THRESHOLD` (default `0.8`) — establish the
real baseline on your provider before treating that number as a gate.

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

Manual release evidence remains required for WhatsApp, Slack, Discord, Telegram
and email: authenticate a staging account, send and receive one authorized
message, confirm an unauthorized sender is refused, and verify
disconnect/reconnect.

## Interpreting expected skips

These are normal on a clean machine and not failures:

| Skip | Why |
|---|---|
| `web_search REAL query` | all backends failed, or ddgs rate limited |
| `configured search backend reachable` | no `search_base_url` is configured (the default), or the configured instance is unreachable from this machine |
| `REAL native sandbox` | sandbox runtime not installed |

If the sandbox runtime is missing, checks that exercise shell and permission
behaviour report `a sandbox is required to run code` instead of completing.
That is the isolation rule working as designed — install the runtime rather than
changing the check.

## No CI

There is **no CI**. GitHub Actions is blocked by a billing issue on this
account, so a workflow was written and then removed rather than left
permanently red. Everything above runs locally and for free — run it before
opening a PR.
