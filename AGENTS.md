## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## ponytail

Lazy senior dev mode. The best code is the code never written. Active by
default at the `full` level (switch: `/ponytail lite|full|ultra|off`).

Before writing any code, stop at the first rung that holds:

1. Does this need to be built at all? (YAGNI)
2. Does it already exist in this codebase? Reuse the helper, util, or pattern that's already here, don't re-write it.
3. Does the standard library already do this? Use it.
4. Does a native platform feature cover it? Use it.
5. Does an already-installed dependency solve it? Use it.
6. Can this be one line? Make it one line.
7. Only then: write the minimum code that works.

The ladder runs after you understand the problem, not instead of it: read the task and the code it touches, trace the real flow end to end, then climb.

Bug fix = root cause, not symptom: a report names a symptom. Grep every caller of the function you touch and fix the shared function once — one guard there is a smaller diff than one per caller, and patching only the path the ticket names leaves a sibling caller still broken.

Rules:

- No abstractions that weren't explicitly requested.
- No new dependency if it can be avoided.
- No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins, but only once you understand the problem. The smallest change in the wrong place isn't lazy, it's a second bug.
- Question complex requests: "Do you actually need X, or does Y cover it?"
- Pick the edge-case-correct option when two stdlib approaches are the same size, lazy means less code, not the flimsier algorithm.
- Mark deliberate simplifications that cut a real corner with a known ceiling (global lock, O(n²) scan, naive heuristic) with a `ponytail:` comment naming the ceiling and upgrade path.

Not lazy about: understanding the problem (read it fully and trace the real flow before picking a rung, a small diff you don't understand is just laziness dressed up as efficiency), input validation at trust boundaries, error handling that prevents data loss, security, accessibility, the calibration real hardware needs (the platform is never the spec ideal, a clock drifts, a sensor reads off), anything explicitly requested. Lazy code without its check is unfinished: non-trivial logic leaves ONE runnable check behind, the smallest thing that fails if the logic breaks (an assert-based demo/self-check or one small test file; no frameworks, no fixtures). Trivial one-liners need no test.

## engineering

From `docs/Practical-Software-Engineering-Field-Guide (2).md` — follow these on every change.

**Done** means: code works for the intended case and the obvious edge cases; tests exist that would fail if someone broke this in six months; the local gates are green (there is no hosted CI on this repo — run `scripts/release_check.py` and the suites in `docs/wiki/12-testing-and-verification.md`); the change is reviewed; anything a future reader needs is written down; it has been observed working somewhere other than the author's laptop; nothing left commented-out, no debug prints, no `TODO: fix later` without a linked issue.

**Commits** — one logical change per commit that leaves the repo working. Conventional shape: `<type>: <imperative summary, <=50 chars>`; body explains *why*, not what the diff already shows. Run `git status` and `git diff --staged` before every commit.

**History** — never rewrite history or `push --force` on a branch other people have pulled; use `--force-with-lease`. Rewriting history is only safe on branches only you have.

**PRs** — a PR is a unit of review, not a unit of work; keep changes small (<400 lines) and split large ones. Diff contains only what the task required (read the full file list). Description says what, why, how to verify, and what you are unsure about. No secrets, keys, or real user data anywhere in the diff.

**Scope** — unrelated issues spotted while working get written down (open an issue, note in the PR), never fixed in the same PR.

**Security** — never commit secrets; if one leaks, rotate it immediately and report it. Validate model output before acting on it — never pass it raw to `eval`, a shell, SQL, or a file path. Least privilege on tools; never combine private data + untrusted input + an outbound channel in one agent.

**Tests** — test contracts, not implementation. Name states the behaviour; one behaviour per test; independent and order-free; deterministic. A new test must fail when the code is broken — verify by breaking it deliberately. Fix or delete flaky tests; never ignore them.

**AI specifics** — prompts are source code: own files, versioned, reviewed. Pin exact model versions, never `-latest`. Instrument every model call (input/output/cached tokens, cost, latency, `stop_reason`, model id). Bound `max_tokens`; alert on `stop_reason == max_tokens`, output-parse failure, and cost spikes.

**Reviewing agent code** — check for plausible-but-wrong APIs, silently swallowed errors, tests that mock the thing under test, scope creep, confidently invented config, and duplicated logic where an existing helper fits.

## testing

Every code change must have tests that would fail if the logic broke. The test suite lives in `tests/` and uses pytest.

**Test structure:**

| Directory | What it covers |
|---|---|
| `tests/gateway/platforms/` | Per-adapter unit tests (Slack, WhatsApp, Discord, Telegram, Email) |
| `tests/gateway/` | Gateway runner, auth, session store, agent bridge |
| `tests/test_mcp.py` | MCP client (connecting to external servers) |
| `tests/test_mcp_server.py` | MCP server mode (exposing tools to external agents) |
| `tests/test_env_key_store.py` | `.env` key store, migration, masking, secret resolution |
| `tests/test_permission.py` | Permission modes, escalation, grants, path zones |
| `tests/test_audit_fixes.py` | Security hardening, shell guards, git blocks, calculator safety |
| `tests/test_cli_setup.py` | Setup wizard, model picker, custom provider config |
| `tests/test_subagents.py` | Subagent specs, tool-call parsing, reasoning strip, secret redaction |
| `tests/test_providers.py` | Multi-model provider registry, API key precedence, model switching |
| `tests/test_security_fixes.py` | Sensitive file blocks, host shell gating, git mutation blocks |
| `tests/test_ssrf.py` | SSRF guard, IP validation, redirect re-checking |
| `tests/test_http_search.py` | HTTP tools, template formatting, search tool declarations |
| `tests/test_new_tools.py` | Cron, Docker sandbox, browser, native sandbox, structured git |
| `tests/test_skills.py` | Skill package loading, tool merging, core-tool protection |
| `tests/test_persona.py` | Persona rendering, frontmatter stripping |
| `tests/test_images.py` | Image message building, MIME inference, sensitive symlink block |

**What to test for each type of change:**

| Change type | What to test |
|---|---|
| **New adapter** (Slack/Discord/WhatsApp/Email) | Imports, config reading, markdown conversion, send/edit, streaming support, allowlist integration, automated-sender handling |
| **Gateway runner change** | Slash commands (`/approve`, `/deny`, `/new`, `/help`), session queueing, turn lock, adapter registration in `build_runner()` |
| **Permission/security change** | `check_permission()` for each mode, escalation flow, grant lifecycle, hard-blocked commands, path zones, sensitive files |
| **MCP change** | Tool registration, curated subset (dangerous tools excluded), handler signature synthesis, transport config, tool dispatch via `run_tool()` |
| **Config/env change** | `.env` load/update, migration, masking, secret resolution precedence, allowlist from config |
| **Engine change** | `find_tool_calls()` parsing (all formats including `<\|mask_start\|>`), `run_tool()` dispatch, `run_agent()` loop, escalation retry, unknown-tool recovery |
| **CLI change** | Setup wizard flow, provider picker, gateway setup, `--mcp-serve` flag, `--mode` flag |

**Test rules:**

1. **Unit test every function with non-trivial logic** — branches, loops, parsers, permission gates, format converters. One-liners that pass through to stdlib need no test.
2. **End-to-end pipeline test for each feature** — e.g. email adapter: IMAP poll → parse → allowlist check → dispatch to runner → agent processes → SMTP reply. Mock external services, test the full chain.
3. **Test contracts, not implementation** — assert behavior, not internal variable names. If you refactor, tests should still pass.
4. **One behavior per test** — test name states the behavior: `test_email_process_message_drops_unauthorized_sender`, not `test_email_1`.
5. **Independent and order-free** — no test depends on another test's side effects. Use `tmp_path` for filesystem, `monkeypatch` for env/config.
6. **Deterministic** — no `time.sleep`, no network calls, no random without a seed. Mock `asyncio.to_thread`, `imaplib.IMAP4_SSL`, `smtplib.SMTP`.
7. **Verify the test fails when code is broken** — break the code deliberately, run the test, confirm it goes red. Then fix the code and confirm it goes green.
8. **Mock external services, not the code under test** — mock `imaplib`/`smtplib` for email, `httpx` for WhatsApp, `discord.Client` for Discord. Never mock the function you're testing.
9. **Test edge cases** — empty input, missing config, unauthorized sender, connection failure, timeout, large message, special characters in paths.
10. **Run before committing** — `pytest tests/ -q` must pass.
11. **Gate platform-specific assertions** — a test that asserts POSIX behaviour (mode bits, `/bin/sh`, crontab paths, shell-rc cleanup) needs a `skipif` marker rather than an expectation that the reader knows to ignore it.

**How to run tests:**

```bash
# Full suite
.venv\Scripts\python.exe -m pytest tests/ -q

# Gateway only
.venv\Scripts\python.exe -m pytest tests/gateway/ -q

# Single adapter
.venv\Scripts\python.exe -m pytest tests/gateway/platforms/test_email.py -v

# MCP server + client
.venv\Scripts\python.exe -m pytest tests/test_mcp_server.py tests/test_mcp.py -v

# Permission + security
.venv\Scripts\python.exe -m pytest tests/test_permission.py tests/test_security_fixes.py -q
```
