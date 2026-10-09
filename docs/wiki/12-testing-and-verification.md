# Testing & Verification

[← Wiki index](README.md)

Three layers, all runnable offline with no model backend.

| Layer | Command | Covers |
|---|---|---|
| Regression checks | `pytest scripts/installer_checks/` | Installer and runtime compatibility |
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

## 1. Regression checks

Portable installer and runtime compatibility checks:

```sh
uv run --extra dev python -m pytest scripts/installer_checks/ -q
```

These use temporary files and simulated failures rather than personal
configuration or live model credentials. CI runs them on Windows, macOS and
Linux. Live model and browser acceptance are separate: passing these does not
establish that every external provider is available.

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

## 5. Web UI checks

```sh
npm ci --prefix web
npm run test:install --prefix web     # one-time Chromium download
npm test --prefix web
```

Playwright specs in `web/tests/`, run against a locally started Web UI.

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
5. **Write generated files under a temp dir, never the repo root.** Nothing
   enforces this automatically — the discipline is on the test author.

## Pre-PR checklist

Before opening a PR:

1. `git fetch origin` and dry-run the merge for conflicts.
2. **Run the target branch's baseline first**, in a worktree. Without it you
   cannot distinguish "this PR broke something" from "this was already broken."
3. Run the checks above on your branch; compare against that baseline.
4. Run the duplicate-def check.
5. Run the functional checks with an isolated `HOME`.
6. Report pre-existing vs new vs fixed failures separately — and never silently
   pick a side on a check whose *expectation* changed.

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
byte-compile, shell syntax and installer portability checks, the regression
checks, and an install smoke test on Linux, macOS and Windows. The deeper checks
above still run locally — run them before opening a PR.
