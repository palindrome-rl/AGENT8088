# Testing & Verification

[← Wiki index](README.md)

Three layers, all runnable offline with no model backend.

> This public release branch deliberately omits the root Python `tests/` directory.
> The unit-test commands below apply to a separate maintainer checkout;
> they are not installation or runtime requirements. Feature-verification
> scripts and the Web UI's separate `web/tests/` remain in this branch.

| Layer | Command | Covers |
|---|---|---|
| Unit tests (development checkout) | `uv run python -m pytest tests/` | Permission layer, tools, gateway, MCP |
| Feature verification | `scripts/verify_features.py` | Real behaviour in temp repos and sandboxes |
| Exhaustive verification | `scripts/verify_everything.py` | Tool specs, shell-classifier matrix, CLI surface |

`--prompt-file` runs one unattended, full-auto turn. Use it only in a trusted,
isolated environment.

## Prerequisites

Install the sandbox runtime before running anything here:

```sh
agent8088 --sandbox-setup     # or: start Docker
```

Agent8088 refuses to run commands with no isolation available — see
[No unsandboxed fallback](06-sandboxing.md#no-unsandboxed-fallback) — so the
checks that exercise shell and permission behaviour cannot complete without it.

## 1. Unit tests

Maintainers need a checkout that includes the root `tests/` directory before
running this section. The public `AGENT8088-v1.2` branch does not include it.

```sh
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/ -q
```

**`AGENT8088_CONFIG=/nonexistent` is not optional.** It forces packaged-default
loading so tests never read — or write — your real `config.txt`. Without it a
test run can pick up and mutate your live configuration.

`tests/` is a **flat directory** — over 150 files, no `tests/gateway/` or
`tests/memory/` subdirectories, and no single `test_permission.py` /
`test_providers.py` / `test_mcp.py` per topic. Coverage for a topic is usually
spread across several specifically-named files; `pytest -k` is the fastest way
to find them:

```sh
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/ -k "turn_budget" -q   # e.g. token/cost/wall-clock ceilings
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/ -k "memory" -q        # store, recall, mem0, forget
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/ -k "gateway" -q       # gateway core + startup
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/test_mcp_server.py -q  # MCP server
```

(Verified against the maintainer test checkout's `tests/` listing — file names churn as tests are
added, so treat any specific filename here as illustrative, not a contract.)

### Gateway extras are required

`test_gateway_core.py` and `test_gateway_startup.py` import the Slack/Discord
adapters directly, so without the `gateway` extra they fail at **import**
rather than skipping — which looks like real breakage but is a missing
optional dependency:

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
5. **Write generated files under a temp dir, never the repo root.** A handful
   of tests (e.g. `test_edit_file_tool.py`, `test_generate_tests_tool.py`)
   write to an `artifacts/`-named path, but there is no shared `artifacts_dir`
   fixture and no repo-wide guard enforcing this — `tests/conftest.py` is 26
   lines and only loads the `engine` module fixture. The discipline is on the
   test author, not automated.

## 5. Live-model tool-choice scoring

The old optional scorer depended on a missing `tests/data/` fixture and could
not run. It is not included in this release. To assess tool choice, test the agent
with an isolated configuration and record the prompts, tool calls, and answers.

## Pre-PR checklist

Before opening a PR:

1. `git fetch origin` and dry-run the merge for conflicts.
2. **Run the target branch's baseline first**, in a worktree. Without it you
   cannot distinguish "this PR broke something" from "this was already broken."
3. Run your branch's full suite; compare against that baseline.
4. Run the duplicate-def check.
5. Run the functional suite with an isolated `HOME`.
6. Report pre-existing vs new vs fixed failures separately — and never silently
   pick a side on a test whose *expectation* changed.

## Public-release gate

`scripts/release_check.py` requires the Python suite. It fails clearly on a
fresh public release checkout; maintainers run it from their test checkout before promoting a release.

Before publishing, maintainers run the strict local gate from their test checkout
(CI covers lint, static checks and an install smoke test, not this full gate):

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

## CI

GitHub Actions runs `.github/workflows/ci.yml` on every push and pull request:
Ruff (syntax errors and undefined names), the duplicate-definition check, a
byte-compile, shell syntax and installer portability checks, and an install
smoke test on Linux, macOS and Windows. The deeper checks above still run
locally — run them before opening a PR.
