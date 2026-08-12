# CLAUDE.md

Project instructions for Agent8088. See `AGENTS.md` for setup and commands, and
`docs/wiki/` for the full reference.

## Testing: never test against real files or real state

**Always create throwaway files in a controlled environment. Never test against
real user data, real config, or files that matter.**

This is not a style preference. A bare CLI invocation once ran the one-time
`api_key` → `.env` migration against a real `~/.agent8088/config.txt`, and a
placeholder key written during testing silently overrode a working one for an
hour. Both were "I'll just try it quickly" moments.

Concretely, for anything you run:

- **Files:** write only into `artifacts/` (use the `artifacts_dir` fixture) or a
  `tmp_path`. Never the repo root, never the user's home, never a real project
  file. A session guard in `tests/conftest.py` fails the run if anything lands
  in the repo root.
- **Config:** `AGENT8088_CONFIG=/nonexistent` for tests. For anything that needs
  real settings, copy the values you need into a temp config — do not point the
  tool at `~/.agent8088/config.txt`.
- **Home:** `AGENT8088_HOME="$(mktemp -d)"` for every verification script and
  every CLI invocation, so writes, key stores, and migrations land in a temp
  directory.
- **Commands:** mock `subprocess.run` rather than executing real mutating
  commands. Mock the external service (`imaplib`, `smtplib`, `httpx`,
  `discord.Client`) — never the function under test.
- **Credentials:** never copy a real key into a file you create, and never print
  one. Pass it through an environment variable and read it at the point of use.
- **Destructive checks:** if a test needs to prove something dangerous is
  blocked, assert on the refusal — do not actually perform the dangerous thing
  against real state.

If a test cannot be made safe this way, say so and ask before running it. A test
that damages real state has already failed, whatever it reported.

## Verifying a change

A change is not "done" until:

1. The full suite passes: `AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$(mktemp -d)" uv run --extra dev --extra gateway python -m pytest tests/ -q`
2. `scripts/verify_features.py` matches the baseline on the branch you started
   from — compare before calling any failure a regression
3. `scripts/check_duplicate_defs.py` is clean after touching `engine.py` or `cli.py`

There is no CI on this repo, so these are the gate.

## Claims must be backed by output

Do not report a test as passing without having run it, and do not describe
behaviour as working without having observed it. If a check was skipped or a
result was inconclusive, say that plainly instead of rounding up.
