# Agent8088 Changelog

All notable changes to the Agent8088 project, organized by feature area.

---

## Plan Mode

`/plan` now means what it means in Claude Code, Hermes and Codex: enter plan mode,
research read-only, propose one plan, get it approved, run it, come back.

### Changed

- `/plan` is a mode, not a one-shot wrapper. It used to flip to plan-only for
  exactly one message and restore the previous mode in a `finally`, so there was
  no state in which a plan could be reviewed, approved and then run. It now holds
  across turns, and `/mode plan-only` starts the same session.
- Approving a plan switches the permission mode, the plan runs through the
  ordinary tool path, and the session returns to the mode it had before `/plan`
  once the work is done. Approve with `a` to run it, or `e` to be asked before
  each edit.
- `set_permission_mode()` is the single funnel for mode changes, so no grant
  outlives the mode that authorized it.
- The prompt shows `plan` while plan mode is active, and `/model plan-only` now
  points at `/mode` instead of dead-ending on "unknown provider".

### Added

- `/audit [on|off]` turns step verification on or off from the prompt, and writes the
  choice through to `config.txt` so it survives a restart. It was reachable only by
  editing `plan_audit` and relaunching, which is the wrong shape for this setting:
  verification is something you want to try on one task, see what it cost, and then
  decide about. With no argument it reports the current state and the last turn's
  verification share. Turning it on says what it will cost.
- `present_plan` tool: presents a plan as markdown for approval. Plan proposal and
  plan execution used to be the same tool, which meant the only approvable plan
  was a fully-specified JSON step array — something models do not reliably
  produce, so a plan written the way a human reads it halted on its first step.

### Fixed

- Verification follows the work. The auditor only ever hooked `execute_plan`, which
  was the same thing as hooking the plan only because plan mode forced every
  mutation through it. With approval handing execution to ordinary tool calls, the
  default `/plan` flow would have become the one flow `plan_audit=1` did not cover.
  Post-approval calls are now audited too, at depth 0 only — a sub-agent's writes
  are not the plan, and auditing inside the auditor would set it verifying itself.
- The auditor grades against the plan the user approved. An `execute_plan` step can
  declare `acceptance`; an ordinary tool call cannot, and without a criterion the
  auditor only checks that the call took effect — which a write that did the wrong
  thing passes. A `write_file` of `hi` against a plan promising a revenue table now
  fails verification and the file is removed.
- A sub-agent pinned to `permission: readonly` is refused a mutation instead of
  being offered an escalation. Sub-agent escalations reach the user, so "this agent
  only observes" was a question someone could answer yes to — about the file the
  auditor was sent to inspect. Plain readonly mode still escalates; that prompt is
  the approval flow.
- Verification's cost stays visible on the new path: the turn's audit share is
  captured as the budget is torn down, and the CLI reports it.
- A plan the model only described is no longer reported as done. A turn that ends
  in plan mode with nothing approved now says so explicitly.
- Plan mode is no longer one-shot. After an approved plan finished, every later
  mutation was hard-blocked with a message naming a JSON step array, and the model
  retried the direct call until the turn died with "I wasn't able to produce an
  answer" — for every turn thereafter.
- A plan step that names no tool halts with an explicit error instead of being
  classified into an arbitrary tool and handed its own prose as that tool's single
  required argument, which could route plan text to a shell as a literal command.
- `find_tool_calls` parses every batched `✿FUNCTION✿`/`✿ARGS✿` block instead of
  only the first. One greedy regex spanned all the blocks in a reply at once, so
  three batched `write_file` calls arrived as a single unparseable blob and all
  three were lost. Each block's JSON extent is now found by counting braces
  outside string literals, which keeps nested JSON intact.

---

## Long-Horizon Reliability

Both changes target the same failure: a step that did not do what it claimed,
recorded as though it had, with every later step built on top of it.

### Plans halt on the first failed step

- `_exec_plan()` now stops at the first failed step instead of running the rest.
  Previously a failing step appended its error to the output and the loop
  continued — a plan whose first step failed would run all remaining steps
  against a state the plan no longer described.
- New `_plan_step_failed()` recognises two forms: a tool result starting with
  `Error:`, and an unanswered or denied `ESCALATION_REQUEST:` (the request string
  survives only when nobody approved it).
- Unknown tools and missing required arguments now halt too, rather than `continue`.
- The result reports where it stopped and how many steps did not run, so a
  half-executed plan cannot be mistaken for a finished one.
- Not a rollback: steps that already ran stay run.

### Only verified state persists (`plan_audit_revert`, default on with auditing)

Verification previously stopped the *next* step but left the failed one on disk,
so a plan committed what it attempted rather than what it proved.

- A `write_file` step that fails verification is now restored to its exact
  pre-step bytes: created files are removed, overwritten files are put back. The
  snapshot is taken before the step runs, because afterwards the prior state is
  the one thing that cannot be reconstructed.
- Bounded on purpose, and each boundary is reported rather than implied: only the
  failed step is reverted (verified steps stay committed); only `write_file` has
  an inverse, so shell/docker/cron steps report `not reverted — no undo`; files
  over `plan_audit_revert_max_bytes` are not snapshotted and say so.
- Worst case is bounded by construction — a revert restores the bytes that were
  there before the step, so a wrong `fail` verdict costs the step, never data
  predating it.

### Acceptance criteria and evidence per step

- A plan step may declare `acceptance` (what must be true for it to count as
  done) and `evidence` (what to inspect). Both are passed to the auditor, which
  otherwise has to invent a criterion and then grade against its own guess.
- Audit scope is now closure-based rather than permission-based. A declared
  `acceptance` always qualifies a step; otherwise only modes leaving a durable
  trace are audited. `browse_page` dropped out: a rendered page closes over
  nothing, so auditing it bought an inconclusive verdict for the price of a model
  call.
- `execute_plan`'s tool description advertises both fields, or the model would
  never emit them.

### Verification cost is measured, not assumed

- `_TurnBudget` attributes tokens to the spending role (`main`,
  `subagent:<type>`), and each model-telemetry line carries the same `role`.
- An audited plan reports its share: `Verification cost this turn: 22% of tokens
  (4400 of 20000)`.
- Published figures put auditors at roughly a fifth to a third of harness tokens.
  Whether that holds for a given workload is now measurable locally instead of
  taken on faith — which is what makes `plan_audit` a decision rather than a
  guess.

### Opt-in per-step verification (`plan_audit=1`)

- Each *mutating* plan step is verified by the readonly `auditor` sub-agent
  before the plan continues; a `fail` verdict halts the plan on the same path as
  any other failure. Reads are not audited — auditing a read tells you the read
  returned what it returned.
- An auditor that cannot run does **not** halt: an inconclusive result is
  recorded in the output and the plan proceeds. A verifier that cannot reach the
  model must not be able to stop all work by itself.
- Auditing is skipped once the shared `_TurnBudget` is spent. The auditor draws
  on the same budget as the work it checks, so it yields rather than starving the
  task it exists to protect.
- Off by default: one extra model call per mutating step. The case for enabling
  it is the unattended paths (gateway, cron) where nobody is watching.

### Container images are provisioned before a tool's timeout applies

- `_exec_docker_command()` now ensures the image is local before starting a
  container. `docker run` pulls a missing image itself, but inside the *tool's*
  timeout — 20s for the read-only git tools — so the first `git_status` on any
  machine without `alpine/git` died with a bare "Command timed out after 20s"
  naming neither Docker nor the pull. This was the long-standing
  `approved git_status returns real output` failure in `verify_features.py`.
- New `docker_pull_seconds` (default 300) budgets the pull separately. A failed
  pull now names the image and the `docker pull` command that fixes it.
- Presence is cached per image, so the probe costs one `docker image inspect`
  per image per process.

### Windows: suite green, and a real gap closed

- The suite now passes on Windows (previously 7 failures, documented as 2).
  `assert_owner_only()` in `tests/conftest.py` asserts mode 0600 on POSIX and the
  actual ACL on Windows, where `st_mode` reads 0666 on a correctly locked file.
- **`searxng_provision.write_settings()` used `chmod(0o600)`**, which on Windows
  protects nothing — the generated `settings.yml` holds a `secret_key` and kept
  its inherited SYSTEM/Administrators ACL. It now writes through
  `_write_private_text()` like every other private file.
- Genuinely POSIX-only tests (crontab, `/bin/sh` fallback, shell-rc cleanup) are
  now `skipif`-marked with the Windows counterpart named; the symlink test skips
  when the OS denies symlink creation.
- `tests/test_searxng_provision.py` patched `sp.subprocess.run` — the *shared*
  stdlib module — which leaked a docker fake into every other module. It now
  patches the module's own `_run` seam.

### Verification scripts

- `verify_everything.py` read the developer's real `~/.agent8088/config.txt`, so
  whether in-repo checks passed depended on someone's setup wizard. It now pins
  `AGENT8088_CONFIG` to the packaged default.
- Its git checks asserted "runs after explicit host approval" without ever
  granting `local_execution`, so they asserted on an `ESCALATION_REQUEST` string
  — the gate working correctly, reported as the tool failing.
- Its Windows ACL check compared `icacls` output against a raw SID, but icacls
  resolves a granted SID to `DOMAIN\user`, so it could never match.
- Both scripts hardcoded `len(SUBAGENT_SPECS) == 4`; they now assert the bundled
  set is present, so adding a profile does not fail the gate.

### Sub-agent permission floor + `auditor` profile

- Sub-agent profiles accept `permission: readonly` in frontmatter. The sub-run is
  pinned to readonly for its whole lifetime regardless of the caller's mode,
  including `--edit`.
- The floor only restricts. No frontmatter value widens a sub-agent past what the
  caller had — an unrecognised value leaves the caller's mode untouched.
- Pending parent grants (`_one_shot_grant`, `_plan_execution_grant`,
  `_local_fallback_grant`, `_remote_git_grant`) are cleared for the sub-run and
  restored after. Without this, an auditor spawned mid-plan would run inside the
  parent's write grant.
- New `auditor` profile (`read_text`, `execute_shell`, `last_output`, readonly
  floor) verifies a completed step against the environment and returns a
  `VERDICT: pass|fail|unknown` line. Read-only-ness is enforced by
  `check_permission()`, not by the prompt.
- Tests in `tests/test_plan_audit.py`. Note the floor tests use a purpose-built
  profile that *includes* `write_file`: the auditor's own tool list omits it, so
  the tool restriction would block the write before the permission layer was ever
  consulted, and the tests would pass with the floor deleted.

---

## Permission Layer

### readonly → edit Escalation (commits `2f2a3e6`, `e720b88`, `0a7339e`)
- Added `PERMISSION_MODE` global (defaults to `"readonly"` at session start)
- `check_permission()` gates write/shell operations — blocks in readonly, allows in edit
- `run_tool()` returns `ESCALATION_REQUEST:edit:change_type:paths:reason` when blocked
- `grant_escalation()` transitions to edit mode for the session

### Per-Action Prompting (commit `0ad31ca`)
- Changed `grant_escalation()` from session-wide edit mode to one-shot grant
- `_one_shot_grant` flag allows exactly ONE blocked tool to run, then reverts to readonly
- Every new write/folder/system command prompts the user separately
- `--edit` flag still gives permanent edit mode (explicit override)

### Escalation Retry (commit `810bb0f`)
- After user approves, injects `"Permission granted. Retry the tool call that was blocked."` into messages
- After user declines, injects denial message so model informs the user
- `seen.discard(sig)` fix: permission-blocked calls are not cached as "already ran" (commit `129ee7a`)

### Allow/Decline Prompt (commits `810bb0f`, `ffa57e1`, `e935887`)
- Rich `Prompt.ask()` with Allow/Decline choices → simplified to `y/n` (case-insensitive)
- `live.stop()` before prompt, `live.start()` after — fixes input capture inside Live display
- Removed `request_permission_escalation` tool entirely — engine gate generates accurate paths

---

## Tool Alias Resolution (commit `26382a3`)

### Problem
Model (Ornith-35B) emitted natural tool names like `bash` instead of canonical `execute_shell` from `tools.txt`. `find_tool_calls()` did exact match → call silently dropped → raw `✿FUNCTION✿` text displayed as answer.

### Fix
- `TOOL_ALIASES` map: `bash`→`execute_shell`, `search`→`web_search`, `read`→`read_text`, `write`→`write_file`, `calc`→`calculate`, etc.
- `_resolve_tool_name()` resolves aliases before `TOOL_NAMES` check
- All 5 match sites in `find_tool_calls()` use the resolver

## Tool Arg Transforms (commit `a1552ee`)

### Problem
Model called `mkdir({"path": "testing"})` instead of `execute_shell({"command": "mkdir testing"})`. Alias map only resolved the name, not the args format.

### Fix
- `TOOL_ARG_TRANSFORMS` dict of lambdas: `mkdir`→`{"command": "mkdir {path}"}`, `rm`→`{"command": "rm {path}"}`, `cp`→`{"command": "cp {src} {dst}"}`, etc.
- `_resolve_tool_args()` applies the transform when alias resolves to `execute_shell`
- Covers: mkdir, rmdir, touch, rm, cp, mv, cat, echo, ls, grep, find, pwd, chmod, curl, wget, git, pip, python, node

---

## Security Layers (commit `a6ee527`)

### Layer 1: Sensitive File Read Protection
- Hardcoded blocklist: `.env`, `config.txt`, `configb.txt`, `id_rsa`, `.ssh/`, `*.pem`, `*.key`, `*_KEY*`, `*_SECRET*`, `*_TOKEN*`, `*_PASSWORD*`
- `_is_sensitive_path()` checks before `read_text` runs — returns `"Access to sensitive file denied"`
- Config override: `allowed_sensitive_files=.env,secrets.yaml` in `config.txt`

### Layer 2: Network Access Control
- `web_search` and `get_page_title` prompt the user (y/n) on **every** request
- Removed `http_get` from readonly-safe mode list in `check_permission()`
- Shows the full URL being requested in the escalation panel
- One-shot grant: each approval covers one network request only

### Layer 3: Path-Based Write Restrictions (Three-Tier Zones)
- `no_prompt_paths=/tmp` — writes here run without a prompt (auto-approved)
- `prompt_paths=.` — writes to project dir show the y/n escalation
- `blocked_paths=/etc,/home` — writes here are **always blocked**, even in edit mode
- Pre-resolution check for blocked paths (before `resolve_user_path()` ValueError)
- Shell mutating commands (`mkdir`, `rm`, `mv`, `cp`, `touch`) also check blocked paths

---

## Cross-Platform Support (commit `79cdfcb`)

### Windows Shell
- `_exec_shell_command()`: uses `cmd.exe` on Windows, `/bin/bash` on Linux
- `READONLY_SAFE_COMMANDS`: added Windows equivalents (`dir`, `type`, `findstr`, `hostname`, `ver`, `vol`, `tasklist`, `systeminfo`)
- Added cross-platform commands (`python`, `pip`, `node`, `npm`, `curl`, `wget`)

### Cross-Platform get_page_title
- Replaced `grep -oP` (Linux-only Perl regex) with Python one-liner using `urllib` + `re`
- Works on both Windows and Linux

### readline Import
- Made `readline` import safe with `try/except` (Unix-only module)

---

## Config Fixes (commit `e509c32`)

### Relative Paths
- Commented out hardcoded `/home/amir/projects/agent8088/` paths in `config.txt`
- Engine falls back to `APP_DIR` defaults (script's own directory)
- `allowed_paths` uses `.` (resolved to `PROJECT_ROOT` at runtime) instead of hardcoded path
- Fixes: "Tools: 0 loaded" banner, tool calls silently dropped

### Three-Tier Path Zones Config
- `no_prompt_paths=/tmp` — auto-approved writes
- `prompt_paths=.` — y/n prompt for writes
- `blocked_paths` — always blocked (commented out by default)

---

## Rich CLI UI (commit `41f6d91`)

### New File: `agent8088_cli.py`
- Hermes-style interactive interface importing the real `agent8088` engine as a module
- Live token streaming (`on_token` callback, `stream=True`)
- ESC-to-interrupt (`EscListener` + `AgentInterrupted` exception)
- Rich panels, tables, diffs for tool output
- Slash commands: `/tools`, `/tool`, `/plan`, `/raw`, `/model`, `/config`, `/system`, `/history`, `/trace`, `/temp`, `/maxturns`, `/save`, `/clear`
- `--edit` flag for permanent edit mode (no per-action prompts)
- Context-window progress hint (`% ctx` in prompt)

### Engine APIs Added for UI
- `on_token` streaming in `create_completion()` (backward-compatible)
- `interrupt_check` in `run_agent()` (raises `AgentInterrupted`)
- `_last_write_diff` + `_make_diff()` for rich diff display
- `CONTEXT_WINDOW` global (configurable via `config.txt`)
- `on_step` callback in `_exec_plan()` for live plan checklist

---

## Repo Restructure (commit `41f6d91`)

### Clean Folder Structure
- `configs/` — model-config variants (`reality7b_config_colossus.py`, `reality7b_config_ollama.py`)
- `scripts/` — one-off repo ops (`configure.sh`, `push-to-github.sh`, `verify-push.sh`)
- `research/` — non-runtime pipeline (`skillopt.py`, `run_benchmark.py`, `data_cleanup/`, `vast-training/`, `paper/`)
- `docs/` — design specs and plans
- `skills/` — 20 agent skill YAMLs (unchanged)

### Removed (24 files + 2 dirs)
- 16 toy algorithm scripts (factorial.py, fibonacci.py, gcd.py, etc.)
- 2 unrelated web projects (`amirweb/`, `projects/ahweb3/`)
- 2 one-time deployment docs (`DEPLOYMENT_STATUS.md`, `GITHUB_SETUP_INSTRUCTIONS.md`)

### Path Fixes in Moved Files
- `research/skillopt.py`: `config.txt`/`system.md` resolve to `APP_DIR.parent` (repo root)
- `research/run_benchmark.py`: hardcoded `/home/amir/projects/agent8088` replaced with `Path(__file__).resolve().parent.parent`

---

## Backward Compatibility (commit `0efdc8a`)
- Old `./agent8088` REPL defaults to edit mode (no behavior change)
- `run_benchmark.py` sets `AGENT8088_PERMISSION=edit` via `os.environ.setdefault()`
- `AGENT8088_PERMISSION=edit` env var works for any caller
- All new engine params default to `None` — existing callers unaffected

---

## Graphify Integration
- `graphify opencode install` — AGENTS.md always-on section + `.opencode/plugins/graphify.js`
- Graph refreshed after every code change (`graphify update .`)
- 307 nodes, 375 edges, 32 communities (latest)
- Gemini-powered semantic extraction (38,690 + 35,064 tokens across 2 runs)
---

## Guardrails (2026-08-06)

Seven guardrails closing gaps found by auditing the existing safety layer against
the OpenClaw and Hermes harnesses. **Every one defaults to off or permissive, so
an existing `config.txt` behaves exactly as before.**

### Turn budget — tokens, cost, wall clock
- `create_completion` never read `response.usage`; there was no spend accounting anywhere
- `max_turns` bounded *rounds*, not resources — a plan or subagent chain was unbounded inside a few rounds
- `_TurnBudget` checked at the top of each round, *before* the model call, so an exhausted budget costs nothing
- Returns the partial result plus the name of the key to raise, rather than discarding work
- Subagents inherit the parent budget via `_active_budget` — a fresh budget would be a free bypass
- Streaming responses carry no usage object, so tokens fall back to a chars/4 estimate
- Keys: `max_turn_seconds`, `max_turn_tokens`, `max_turn_cost_usd`, `cost_per_1k_input`, `cost_per_1k_output`

### Egress domain policy
- `_ssrf_check` covered internal addresses only; every public host was reachable and `http_post` could send anywhere
- `SANDBOX_ALLOWED_DOMAINS` existed but was sandbox-scoped, not applied to the agent's own tools
- `_egress_check` wired into all seven outbound paths, including HTTP redirects and in-browser subresource requests
- Host matching is dot-anchored, so `evilpastebin.com` does not match `pastebin.com`
- Ordered *before* `_ssrf_check`: the policy is a pure string check while SSRF calls `getaddrinfo`, and resolving a rejected host leaks the attempt to that domain's nameserver
- Keys: `allowed_domains`, `blocked_domains`

### Outbound secret guard
- `_redact_secrets` protected tool *output*; nothing stopped the model putting a credential into an `http_post` body or URL
- With unrestricted egress this was the full lethal trifecta: private data, untrusted content, outbound channel
- `_outbound_secret_check` is a hard floor before the permission gate — no mode unlocks it, including `full-auto`, and there is no escalation path
- The error never echoes the matched value; a 12-char minimum avoids false positives on short config values

### Append-only audit log
- `_log.info` went to a logger with no configured sink — no durable record of what ran or what was refused
- `_audit` writes one redacted JSON line per gated decision (`allowed` / `blocked` / `denied`) at mode 0600
- Every field passes through `_redact_secrets`, so a blocked exfiltration attempt is recorded without writing the credential to disk
- Never raises: an unwritable sink is a lost record, not a failed turn
- Keys: `audit_log`, `audit_log_path`, `audit_max_detail`

### Gateway rate limiting
- No throttling existed at all; every turn serializes behind one global lock, so one user in a loop starved the queue
- `_RateLimiter` is a per-user sliding window applied before slash-command handling — otherwise `/help` is a free flood channel
- Rejected hits are not recorded, so a user who keeps hammering drains out of the window rather than being locked out
- Key: `gateway_rate_limit_per_min` (default 20, `0` disables)

### Shell command allowlist
- `deny_commands` only stops what you thought of; `allow_commands` stops everything you did not (Hermes-style approval patterns)
- Enforced at the hardline floor, so an unlisted command is not escalatable
- Precedence: unrecoverable floor > `deny_commands` > `allow_commands`. `allow_commands=*` cannot re-enable `rm -rf /`
- Covers wrapped payloads (`bash -c '<unlisted>'`) via the existing `_hard_blocked_shell` recursion
- Key: `allow_commands`

### Write blast radius
- The permission layer decided *whether* a write was allowed; nothing bounded how many or how big
- Checked before the permission gate, so the refusal is not something a user can wave through by mistake
- Reset only by the outermost `run_agent`, so a subagent cannot hand itself a fresh write budget
- Keys: `max_writes_per_turn`, `max_write_bytes`

### Inbound gateway text sanitizing
- Gateway text reached `run_agent` raw, so `<|im_start|>system` in a chat message was tokenized as a real role boundary by self-hosted ChatML/Llama templates — a plain message could forge a system turn
- The engine already stripped these from fetched pages and MCP responses; gateway text was the one untreated path in
- Deliberately *not* `_wrap_untrusted`: the allowlisted sender is the principal, so demoting their whole message to "data, never instructions" would stop the gateway acting at all
- Imported directly rather than through the `A` module so patching the engine in a test cannot silently disable the sanitizer

---

## Capability Self-Introspection (2026-08-06)

- "What tools do you have?" got a guess from the model's reading of the prompt; "what is your configuration?" got a flat refusal, because `_PROTECTED_TARGET_RE` matched `your config`
- `describe_capabilities()` builds the answer from live state: `TOOL_SPECS` grouped by access mode, `MCP_RUNTIME.statuses` with per-server state and tool lists, skills, subagents, resolved sandbox backend, every limit (including which are **not** set), and the always-on floor
- Generated rather than hand-maintained, so it cannot drift from what the agent actually has
- New `introspect` tool mode, permitted in **every** permission mode — it opens no file, socket, or process, and an agent that cannot say what it can do is least useful exactly when most restricted
- Output goes through `_redact_secrets` and contains no system-prompt text
- Same function on every surface, so the human and the model never see different answers:
  - `describe_capabilities` tool (model)
  - `/capabilities` (CLI)
  - `/capabilities` (gateway chat)
  - default non-mutating MCP server surface
- Narrowed `_PROTECTED_TARGET_RE` to drop `config`/`configuration`: refusing an ordinary capability question was worse than the disclosure it guarded, since the real secrets are covered by `_is_sensitive_path`, `_redact_secrets`, and `_is_system_leak` regardless. Asking for `config.txt` or the system prompt by name is still refused — tests cover both directions.

### Tests added
`tests/test_turn_budget.py` (12), `tests/test_egress.py` (12), `tests/test_exfil_guard.py` (8),
`tests/test_audit_log.py` (11), `tests/test_command_allowlist.py` (15),
`tests/test_capabilities.py` (32), `tests/gateway/test_rate_limit.py` (11),
plus 2 inbound-sanitizing tests in `tests/gateway/test_agent_bridge.py`.

---

## Hermes-Parity Guardrails (2026-08-06)

Sourced from the live Hermes Agent documentation via the Context7 MCP server
(`/nousresearch/hermes-agent`), then checked against Agent8088's actual code.

### Fixed: unbalanced quote bypassed the always-on git floor

`_hard_blocked_shell` lexes the command to find dangerous git operations and
wrapper payloads. On a `ValueError` it returned `False`, so every lexer-based check
below it silently passed — a remote-push command was refused as written, but
appending one unbalanced double-quote made it execute. Same trick worked for
`reset --hard`, `clean -fd`, `branch -D`, `checkout --`, `stash drop`, and inside a
`bash -c` payload. It held in `edit` and `full-auto`, where the floor is the only
thing left.

Adopts Hermes' two mechanisms (`tools/approval.py`):
- `_command_parser_limit_exceeded` — a command too long (`max_command_chars`) or
  too quote-dense to analyse is treated as dangerous rather than skipped
- `_command_detection_variants` — detection re-runs on a de-quoted variant, so it
  no longer depends on well-formed input

### Deliberately not mirrored: Hermes' `approvals.mode`

Hermes has `smart | manual | off`, where `smart` uses an auxiliary guardian model.
Not adopted: `PERMISSION_MODE` already decides what is gated, so a second axis that
can also wave a gate through means `PERMISSION_MODE=readonly` plus one other key
silently behaves like `full-auto` — a second, less obvious route to full-auto via a
key that never says "full-auto". `manual` and `off` already have exact equivalents
(`readonly`, `full-auto`).

### Denial circuit breaker (`denial_breaker_threshold`, default 3)
- A denied action left the model free to re-propose it every round until
  `max_turns` — reads as the agent ignoring the user, and spends a whole turn
  budget to reach the same no
- After N consecutive denials the request ends with the model told to stop and
  report. One approval resets the count; the count resets per request

### Unattended-run policy (`cron_mode`, default `deny`)
- A scheduled run has no operator, so an `ESCALATION_REQUEST` was emitted to nobody
  and sat until the turn died
- `deny` refuses and tells the model to report it; `approve` treats the gate as
  granted. Neither touches the always-on floor
- Crontab entries and Windows task scripts set `AGENT8088_UNATTENDED=1`
- Read once at import, not per call — an env-var check on the hot path is a
  prompt-injection escalation route, which is why Hermes freezes its equivalent

### MCP server circuit breaker
- 3 consecutive failures open a per-server breaker for a 60s cooldown; while open,
  the error explicitly tells the model not to retry and how long is left
- Success resets it; breakers are per server, so one dead server does not silence a
  healthy one

### Destructive command confirmation
- `destructive_slash_confirm` (default on): `/reset` and `/clear` ask before
  discarding a conversation
- `mcp_reload_confirm` (default on): `/mcp reload` asks before dropping the tool
  cache
- Skipped when there is nothing to lose, and when stdin is not a tty

### Tests added
`tests/test_shell_parser_failclosed.py` (29), `tests/test_approvals.py` (25),
`tests/test_mcp_breaker.py` (9), plus 2 capability-report tests.

---

## Web Search Provider Registry (2026-08-10)

One `web_search` tool, four interchangeable backends selected by config instead
of by the model picking a per-vendor tool.

- **New `src/agent8088/web_search.py`** — provider registry, four backends, and a
  runtime fallback chain. Selection precedence follows Hermes'
  `agent/web_search_registry.py`: explicit `web_search_provider`, then a single
  available backend, then `searxng -> tavily -> exa -> ddgs` filtered by
  availability.
- **Runtime fallback.** Hermes only *selects* a backend; `run_search()` also
  falls *through* the chain, so a configured-but-broken instance does not mean
  "no web search". The output always names the serving backend, so a silent
  fallback stays visible.
- **New `src/agent8088/searxng_provision.py`** — `/search setup` provisions a
  container bound to `127.0.0.1` only (the JSON API is unauthenticated) with a
  generated `settings.yml` that enables `search.formats: [json]` and disables the
  bot limiter. Both are off/on by default upstream and make the API unusable.
- **New `mode=search`** in `run_tool`, gated like the other network modes.
  `SAFE_MODES` treats it as non-mutating, same standing as `http_get`.
- **New `/search`** — `status`, `setup`, `stop`, `doctor`, `use <backend>`.
- **Wizard** now offers a Docker-aware picker instead of a bare URL field, with
  **Keep current setting** when a URL is already configured.
- **`ddgs>=9,<10` is now a hard dependency.** A fallback that might need
  installing first is not a fallback. Web search works on a fresh install with
  no Docker, no key, and no setup.
- **Fixed:** the engine seeds a default `search_base_url` into `APP_CONFIG` so
  tool templates interpolate, which made the SearXNG backend claim availability
  on every machine — shadowing the fallback and misreporting in
  `/capabilities`. `SEARCH_BASE_URL_CONFIGURED` now separates a user-set URL
  from the default.

### Security

- Every backend runs the existing egress/SSRF/outbound-secret guards before each
  request, injected via `SearchContext` so `engine.py` stays the single
  enforcement point.
- `ddgs` owns its own HTTP client and so sits outside `_exec_http`'s guard. Its
  complete fixed host set is checked against the egress policy *before* the
  library runs, and it **fails closed** rather than bypassing an
  `allowed_domains` policy. A guard denial is non-retryable and stops the chain —
  falling through would route around a policy decision rather than an outage.
- A remote SearXNG must use `https://`; plaintext `http://` is accepted only for
  loopback and private hosts.
- API keys resolve from the `.env` store, never `config.txt`, and each backend
  only ever receives its own key.

### Migration

`web_search_tavily` and `web_search_exa` are removed **as tool names only** —
both remain fully supported backends behind `web_search`:

| Before | Now |
|---|---|
| `web_search_tavily` + `tool_headers.*` / `tool_body.*` in `config.txt` | `TAVILY_API_KEY` in the `.env` store (optionally `web_search_provider=tavily`) |
| `web_search_exa` + `tool_headers.*` / `tool_body.*` | `EXA_API_KEY` in the `.env` store (optionally `web_search_provider=exa`) |

The shipped `config.txt` never contained those `tool_*` keys, so no default
install had these tools working.
