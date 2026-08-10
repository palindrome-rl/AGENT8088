# AGENTS.md

Agent8088 — local AI agent with an enforced permission layer. One engine
(`src/agent8088/engine.py`), four front ends (CLI, messaging gateway, MCP
server, cron). Python ≥3.10, built with hatchling, packaged via `uv`
(`uv.lock` is the lockfile). Active branch is `development`.

Long-form reference: `CLAUDE.md` (architecture, security layers, full testing
matrix). This file is the compact version — only what an agent would miss
without help.

## Setup

```bash
uv sync --extra dev --extra gateway
```

- `dev` → pytest, ruff, pip-audit. `gateway` → slack-bolt, slack-sdk, httpx,
  discord.py (optional extras, NOT core deps).
- A bare `uv sync` gives a venv with **no pytest** and **no gateway libs** —
  tests will hard-fail at import. Always sync with the extras you need.
- Creates `.venv\Scripts\python.exe` (Windows) / `.venv/bin/python` (Unix).

## Run the agent (dev)

**Never run the CLI bare.** `agent8088` / `python -m agent8088.cli` reads and
writes `~/.agent8088/config.txt` and `~/.agent8088/.env` by default — a bare
invocation once triggered the one-time `api_key` → `.env` migration against a
real config. Always isolate:

```bash
AGENT8088_CONFIG=/nonexistent AGENT8088_HOME=/tmp/agent8088-sandbox python -m agent8088.cli ...
# flags: --setup | --gateway | --mcp-serve [--mcp-http --mcp-port 8931] | --mode readonly|full-auto|plan-only | --edit
```

A `PreToolUse` hook (`.claude/hooks/guard-agent8088-cli.sh`) backstops this in
session, but a fresh session may not have the hook loaded — isolate every time.

## Tests

```bash
# Full suite (Windows)
AGENT8088_CONFIG=/nonexistent .venv\Scripts\python.exe -m pytest tests/ -q

# Full suite (Unix)
AGENT8088_CONFIG=/nonexistent .venv/bin/python -m pytest tests/ -q

# Single file
AGENT8088_CONFIG=/nonexistent .venv\Scripts\python.exe -m pytest tests/test_permission.py -v

# Single test
AGENT8088_CONFIG=/nonexistent .venv\Scripts\python.exe -m pytest "tests/test_permission.py::test_check_permission_blocks_write_in_readonly" -v

# Functional verification (real code paths, no mocks; SKIPs missing deps)
VERIFY_HOME="$(mktemp -d)"; AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" .venv\Scripts\python.exe scripts\verify_features.py

# Duplicate-definition scan — run after touching engine.py or cli.py
.venv\Scripts\python.exe scripts\check_duplicate_defs.py
```

**`AGENT8088_CONFIG=/nonexistent` is mandatory, not optional.** It forces
repo-relative path loading so tests never read or pollute your real
`config.txt`. The conftest sets it too, but pass it explicitly.

**No CI on this repo** — GitHub Actions is blocked by a billing issue on the
account. "Green" means: the pytest suite passes (modulo the known Windows
failures below), `scripts/verify_features.py` doesn't regress, and
`scripts/check_duplicate_defs.py` is clean. Don't propose Actions-based
automation. The `.claude/skills/pr-check` skill is the manual PR gate.

**Windows has several platform-related failures that are pre-existing, not
regressions**: file-ownership `0600` checks (POSIX-only permission model),
posix bash fallback, InquirerPy TTY prompts, the cron `unattended` env var,
searxng settings-file perms. Before calling any failure a regression, run a
baseline on the target branch first — see `.claude/skills/pr-check/SKILL.md`.

**Gateway tests need the `[gateway]` extras installed.** Without
`slack-bolt`/`discord.py`/`httpx`, the Slack/Discord platform tests fail at
import with `ModuleNotFoundError` (~19 failures) rather than skipping — that's
a missing optional dependency, not breakage. Check `pip show slack-bolt`
before treating a wall of gateway failures as real.

## Mandatory rules an agent would otherwise miss

1. **Data files live ONLY under `src/agent8088/`** — `tools.txt`, `system.md`,
   `config.txt`, `agents/`, `skills_installed/`. The engine loads them from
   the package dir (`APP_DIR`); the wheel ships only that copy. Never add
   copies at the repo root — they are silently ignored, and edits there do
   nothing. Config resolves: `$AGENT8088_CONFIG` → `~/.agent8088/config.txt` →
   `src/agent8088/config.txt` (first match wins).

2. **`scripts/check_duplicate_defs.py` is load-bearing.** Ruff's `F811`
   missed a real duplicate `_wrap_untrusted` in `engine.py` once (two feature
   branches defined the same function; Python kept only the last). Run it
   after any change to a large shared module (`engine.py`, `cli.py`). The
   `PostToolUse` hook runs the suite on edit as a backstop.

3. **One unresolved precedence disagreement — don't pick a side.**
   `tests/test_providers.py::test_configured_api_key_wins_over_adapter_environment_key`
   is `xfail(strict=False)`. `_provider_api_key()`'s docstring says ".env
   first, then os.environ, then api_key"; the test asserts the opposite. This
   is an open product decision. If asked to touch it, surface the
   disagreement and ask which precedence is intended before changing code or
   test.

4. **`graphify-out/` dirty is expected.** It's a generated knowledge graph,
   not hand-maintained. Dirty files after a hook or incremental update are
   not a bug. See the graphify section below.

## Architecture (1 paragraph)

All four front ends call the same `run_agent()` and `run_tool()` in
`engine.py`; adapters translate transport only and never re-implement
permissions — fixing the permission layer once fixes it everywhere. Security
layering is outermost-first, each layer can only refuse (never grant):
`allowed_paths` → sensitive-file floor → shell-startup floor → write zones →
`check_permission(mode)` → shell classifier → hard git blocks → SSRF guard →
sandbox → output guards. Layers 2, 3, 7 (sensitive files, shell-startup
writes, git push/reset) are the **always-on floor** — no mode and no
escalation grant unlocks them, not even `full-auto`. State on disk, no DB:
`~/.agent8088/config.txt` + `.env` (0600), `mcp.json` (user + project scopes),
`gateway-sessions/`, `USER.md` (persona, read-only). Full detail:
`docs/wiki/03-permissions-and-security.md`.

## Layout

```
src/agent8088/        # the ONLY place data files live — engine.py, cli.py, providers.py, mcp.py, mcp_server.py, gateway/, tools.txt, system.md, config.txt, agents/, skills_installed/
tests/                # ~739 tests; conftest.py sets AGENT8088_CONFIG
scripts/              # verify_features.py, verify_everything.py, check_duplicate_defs.py, release_check.py, sync_wiki.py
docs/wiki/            # 13-page reference — the source of truth (GitHub Wiki is a mirror)
research/             # non-runtime: SkillOpt, benchmarks, training
skills/               # 20 YAML skill packages (installable)
.claude/              # hooks (guard-agent8088-cli, guard-protected-push, run-tests-on-edit) + skills (pr-check, project-conventions) + settings.json
```

`docs/wiki/` is the verified source of truth — the README sometimes lags it.
Sync the GitHub Wiki tab with `python scripts/sync_wiki.py`; never edit the
wiki UI directly (overwritten on next sync).

## Git

- Conventional commits: `<type>: <imperative summary, <=50 chars>`; body
  explains *why*. One logical change per commit, repo working after each.
- **Never push to `main` or `development` without explicit go-ahead this
  turn.** A hook (`.claude/hooks/guard-protected-push.sh`) hard-blocks it from
  the Bash tool. Open a PR and let the user merge.
- `--force-with-lease` only, never bare `--force` on shared branches.
- PRs <400 lines; diff contains only what the task required.

## Engineering

From `docs/Practical-Software-Engineering-Field-Guide (2).md`. Highlights:
test contracts not implementation; one behavior per test; deterministic
(mock externals, never the code under test); a new test must fail when code
is broken — verify by breaking it deliberately. No secrets in diffs. Validate
model output before acting on it. Pin exact model versions, never
`-latest`. Full block: `CLAUDE.md` §Engineering practices.

## Testing matrix

Full per-area coverage table and what-to-test-for-each-change lives in
`CLAUDE.md` §Testing matrix (kept there to avoid drift between two copies).
Short version: unit-test every function with non-trivial logic;
end-to-end pipeline test per feature (mock `imaplib`/`smtplib`/`httpx`/
`discord.Client`, never the function under test); test edge cases (empty
input, missing config, unauthorized sender, connection failure, special
chars in paths).

## Navigation

| Want | Read |
|---|---|
| Architecture, security layers in depth | `CLAUDE.md`, `docs/wiki/11-architecture.md`, `docs/wiki/03-permissions-and-security.md` |
| Every config key | `docs/wiki/02-configuration.md` |
| MCP client + server | `MCP_FEATURES.md`, `docs/wiki/07-mcp.md` |
| Messaging gateway | `docs/wiki/08-messaging-gateway.md` |
| Testing details | `TESTING.md`, `docs/wiki/12-testing-and-verification.md` |
| Pre-PR checklist | `.claude/skills/pr-check/SKILL.md` |
| Non-obvious conventions | `.claude/skills/project-conventions/SKILL.md` |
| Change history by feature | `CHANGELOG.md` |

## graphify

Knowledge graph at `graphify-out/` (god nodes, community structure, cross-file
relationships). Generated, not hand-maintained.

When the user types `/graphify`, use the installed graphify skill or
instructions before doing anything else.

- For codebase questions, first run `graphify query "<question>"` when
  `graphify-out/graph.json` exists. Use `graphify path "<A>" "<B>"` for
  relationships and `graphify explain "<concept>"` for focused concepts.
  These return a scoped subgraph, usually much smaller than `GRAPH_REPORT.md`
  or raw grep.
- Dirty `graphify-out/` files are expected after hooks or incremental updates;
  not a reason to skip graphify. Only skip if the task is about stale/incorrect
  graph output, or the user says not to use it.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review or
  when query/path/explain don't surface enough.
- After modifying code, run `graphify update .` (AST-only, no API cost).

## ponytail

Lazy senior dev mode — the best code is the code never written. Active by
default at `full` (`/ponytail lite|full|ultra|off`). The full ladder and rules
live in the loaded skill; the one-line version: before writing code, stop at
the first rung that holds — YAGNI → reuse in this codebase → stdlib → native
platform feature → installed dependency → one line → minimum that works. The
ladder runs *after* you understand the problem (read the task, trace the real
flow end to end), not instead of it. Bug fix = root cause, not symptom (grep
every caller, fix the shared function once). Not lazy about: input validation
at trust boundaries, error handling that prevents data loss, security,
accessibility, anything explicitly requested.