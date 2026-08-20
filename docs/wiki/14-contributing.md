# Contributing

[← Wiki index](README.md)

## Setup

```sh
git clone https://github.com/palindrome-rl/AGENT8088.git
cd AGENT8088
python -m venv .venv
.venv/bin/pip install -e ".[gateway,dev]"
AGENT8088_CONFIG=/nonexistent .venv/bin/python -m pytest tests/ -q
```

Install **both** extras. Without `gateway`, ~19 platform tests fail at import and
look like real breakage.

## Non-negotiable safety rules

These come from real incidents in this repo, not hypotheticals.

### 1. Never invoke the CLI without an isolated `HOME`

A bare `agent8088 --help` triggers the one-time `.env` key migration against the
developer's **real** `~/.agent8088/config.txt`. It happened.

```sh
HOME="$(mktemp -d)" AGENT8088_CONFIG=/nonexistent python -m agent8088.cli --help
```

### 2. Always set `AGENT8088_CONFIG=/nonexistent` in tests

Forces packaged defaults so tests never read or write real config.

### 3. Never write outside the repo or a temp dir

No touching dotfiles, SSH config, crontab or system files. Use a fake `HOME` in
a tmpdir.

### 4. Mock `subprocess.run` for mutating commands

Verification scripts must not execute real destructive commands.

## Adding a tool

Most tools need **no Python at all** — add a row to `src/agent8088/tools.txt`:

```
my_tool|What it does|mode=http_get|args=query|url=https://api.example.com?q={query_q}|timeout=25
```

Pick an existing `mode` and it inherits that mode's permission gating
automatically. Then:

1. Add a test in `tests/` asserting the spec and, if it has logic, its behaviour.
2. If it's a new `mode`, add gating in `check_permission()` **and** a test for
   each permission mode.
3. Update `expected_tools` in `scripts/verify_everything.py` — it's the single
   source of truth for the inventory, and the count assertions derive from it.
4. Update [Tools](04-tools.md).

Never add a tool that bypasses `run_tool()`.

## Adding a security check

1. Decide the layer — see
   [Architecture](11-architecture.md#security-layering). Layers can only refuse,
   never grant.
2. If it belongs on the always-on floor, prove it holds in **all three** modes
   *and* after `grant_escalation()`. Both properties need explicit tests.
3. Be precise about read vs write. The shell-startup-file guard blocks writes
   only, matched on exact filename, so `.editorconfig` and "read my `.zshrc`"
   still work. Over-broad blocking is its own failure mode.
4. Add the adversarial cases: `sh -c` nesting, `&&`/`;` chaining, symlinks,
   relative paths, redirects.

## Testing standards

Write the failing test first, then the fix — every fix in this repo's recent
history has a repro that failed before and passed after.

- **Establish the target branch's baseline before comparing.** Otherwise you
  cannot tell "I broke this" from "this was already broken." The `pr-check`
  skill does it in a worktree.
- **Don't silently flip a test whose expectation changed.** If code and test
  disagree about intent, that's a decision to surface, not to resolve by editing
  whichever is convenient. If you must park it, `xfail` with a reason explaining
  *why it's unresolved* — and prefer actually resolving it.
- **Prefer real behaviour over mocks** for verification scripts, and report
  unavailable dependencies as explicit skips, never silent passes.

## Before opening a PR

```sh
AGENT8088_CONFIG=/nonexistent .venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/check_duplicate_defs.py
VERIFY_HOME="$(mktemp -d)"; AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$VERIFY_HOME" \
  .venv/bin/python scripts/verify_features.py; rm -rf -- "$VERIFY_HOME"
```

Or invoke the `pr-check` skill, which also does the baseline comparison.

There is **no CI** — GitHub Actions is blocked by a billing issue on this
account. Local runs are the gate.

## Branch and PR conventions

- Branch from `main`.
- **Never push to `main` directly.** Open a PR.
  (`.claude/hooks/guard-protected-push.sh` enforces this from the Bash tool.)
- Conventional commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`.
- In the PR body, state: what changed, the repro that proves it, verification
  output, and anything you deliberately left out.
- Flag behaviour changes that could break an existing setup. A security
  tightening that silently locks users out needs a grace path or an explicit
  call-out.

## Duplicate definitions

```sh
python scripts/check_duplicate_defs.py
```

Python keeps only the *last* definition of a repeated top-level name — silently.
Two `_wrap_untrusted` functions once coexisted in `engine.py`, and ruff's `F811`
did not catch it here. Run the AST check.

## Documentation

- Code-level facts belong in this wiki, kept accurate against the source. Where
  the README drifts, the wiki notes the discrepancy rather than mirroring it.
- Design records go in `docs/superpowers/specs/`.
- Update the relevant wiki page in the same PR as the code change.

### Publishing to the GitHub Wiki tab

`docs/wiki/` is the source of truth. The Wiki tab is a **separate git
repository** (`<repo>.wiki.git`) and a generated mirror — never edit it
directly, since the next sync overwrites it.

```sh
python scripts/sync_wiki.py --dry-run   # verify link rewriting
python scripts/sync_wiki.py             # convert, commit, push
```

The script handles the conversion the wiki requires: `README.md` → `Home.md`,
numeric prefixes dropped, internal `[x](04-tools.md)` links rewritten to
`[x](Tools)`, and a generated `_Sidebar.md`. It fails rather than publishing if
any internal link would end up broken.

Adding or renaming a page means updating the `PAGES` list in the script — that
list also defines sidebar order.

## Project layout

See [Architecture](11-architecture.md) for the module map and the reason every
front end shares one engine.
