# Architecture

[← Wiki index](README.md)

## One engine, four front ends

```
   CLI (REPL)        Gateway            MCP server        Cron
   cli.py            gateway/           mcp_server.py     schedule_task
       │                 │                    │              │
       └─────────────────┴──── run_agent() ───┴──────────────┘
                              engine.py
                                  │
                    ┌─────────────┼─────────────┐
              check_permission   run_tool    MCP client
              (the gate)         (dispatch)   mcp.py
```

The single most important structural fact: **every front end calls the same
`run_agent()` and the same `run_tool()`.** Adapters translate transport details
only. They do not re-implement permissions.

That's why fixing the permission layer once fixes it for the terminal, Slack,
Discord, WhatsApp and MCP simultaneously — and why a front end that *does*
diverge (as the MCP server briefly did by forcing full-auto) is a bug rather
than a design choice.

## Module map

| File | Responsibility |
|---|---|
| `engine.py` | Agent loop, tool dispatch, permission layer, security floors, providers, HTTP/SSRF |
| `cli.py` | REPL, slash commands, setup wizards, rendering |
| `providers.py` | The 12 built-in provider profiles |
| `mcp.py` | MCP **client** — connect external servers |
| `mcp_server.py` | MCP **server** — expose our tools outward |
| `gateway/runner.py` | Inbound routing, approval registry, adapter registration |
| `gateway/agent_bridge.py` | Bridges a chat turn to `run_agent()` |
| `gateway/session.py` | Per-chat JSON session files |
| `gateway/auth.py` | Allowlist, per-platform scoping, WhatsApp id normalisation |
| `gateway/platforms/*.py` | Slack / WhatsApp / Discord / Telegram / Email adapters |
| `memory/store.py` | Memory: SQLite schema, hybrid BM25 + vector search, RRF |
| `memory/embed.py` | Memory: embeddings via the provider's `/embeddings` |
| `memory/extract.py` | Memory: the fact-extraction prompt and its parsing |
| `tools.txt` | Tool registry (data, not code) |
| `system.md` | Base system prompt |
| `config.txt` | Default settings |

## The agent loop

Per user turn, `run_agent()` loops up to `max_turns`:

0. **Recall memory** (outermost turn only) — the last *genuine* user turn is
   searched against the memory store and the best few facts are appended to this
   turn's system prompt. See [Memory](16-memory.md).
1. **Call the model** with history + tool definitions.
2. **Strip reasoning** from the content so chain-of-thought never leaks into the
   answer.
3. **Find tool calls** — native `tool_calls` *or* the fine-tuned text-marker
   format, restricted to the allowed set.
4. **No tool calls?** Check whether the model *tried* to call something that
   doesn't exist. If so, tell it what went wrong and loop (bounded) so it can
   recover. Otherwise this is the final answer.
5. **Deduplicate** — an identical `(name, args)` repeat is served from cached
   output instead of re-running, which breaks loops.
6. **Execute** through `run_tool()` → `check_permission()` → the tool.
7. **Feed the result back**, truncated to `max_tool_output_bytes`, redacted, and
   wrapped if it came from outside.
8. **Guard the answer** before returning — system-prompt leak check, secret
   redaction, markup stripping.
9. **Capture memory** (outermost turn only, after the answer) — one extraction
   call distils durable facts from what the user said and what was answered.

Steps 0 and 9 hang off `run_agent()` rather than the loop itself. The loop has
seven return points, so a capture hook inside it would be silently skipped by the
next one added; `run_agent`'s `finally` cannot be escaped. It also already
distinguishes the outermost turn, which is the scope memory wants — a sub-agent is
handed a delegated task rather than something a human said, so it neither recalls
nor writes.

If the model backend errors mid-turn, the loop returns the best output it has
rather than crashing the session.

## Plans stop at the first failed step

`execute_plan` runs its steps in order and **halts on the first failure**, rather
than running the rest against a state the plan no longer describes. A step counts
as failed when the tool reported an error, or when its escalation went unanswered
or denied.

The result then says where it stopped and how many steps did not run, so a caller
cannot mistake a half-executed plan for a finished one. Continuing past a failure
is what turns one bad step into a cascade: every later step is built on an effect
that never happened, and the transcript gives the failure no more weight than any
other line.

A halted plan is not a rollback — steps that already ran stay run. The caller is
expected to fix the cause and issue a new plan for the remaining work.

### Verifying a step actually landed

A step that reports success has only told you the tool did not raise. Setting
`plan_audit=1` sends each *mutating* step to the readonly `auditor` sub-agent,
which inspects the real environment before the plan continues. A `fail` verdict
halts the plan on the same path as any other failure.

This covers two paths, because a plan's work now happens on both. Steps inside
`execute_plan` are audited there. The tool calls an approved `/plan` makes are
audited in `exec_tool`, at depth 0 only — a sub-agent's writes are not the plan,
and auditing inside the auditor would set it verifying itself. That second path
was not always covered: plan mode used to force every mutation through
`execute_plan`, so hooking `execute_plan` was the same thing as hooking the plan.
Once approval started handing execution to ordinary tool calls, the default
`/plan` flow became the one flow with no verification at all.

An `execute_plan` step can declare `acceptance`; an ordinary tool call has nowhere
to put one, so the **plan the user approved** is passed as the criterion instead.
Without it the auditor grades "did this call take effect", which a write that did
confidently the wrong thing passes. With it, a `write_file` of `hi` against a plan
promising a revenue table comes back:
`VERDICT: fail — the file contains only "hi", not a markdown table of quarterly
revenue with at least four data rows` — and the file is removed.

Verification is not free, so the cost stays visible. `_exec_plan` appends its share
to the plan's output; the post-approval path has no such output, so the share is
captured as the turn's budget is torn down and the CLI prints
`verification cost this turn: N% of tokens`. On a small turn that share is large —
a single audit call against one tiny write measured over 90% — which is the number
to look at before leaving auditing on.

Two deliberate asymmetries:

- A failed verification halts; an auditor that could not run does not. An
  inconclusive result is recorded in the output and the plan proceeds — a
  verifier that cannot reach the model must not be able to stop all work on its
  own.
- Auditing is skipped once the shared turn budget is spent. The auditor draws on
  the same `_TurnBudget` as the work it checks (a fresh budget would be a free
  bypass), so it yields rather than starving the task it exists to protect.

Off by default: it costs one model call per mutating step. The case for turning
it on is the unattended paths — gateway and cron — where nobody is watching and
a half-done plan reported as finished is the expensive failure.

### Only verified state persists

A step *proposes* a change; verification is what *commits* it. With
`plan_audit_revert=1` (the default when auditing is on), a `write_file` step that
fails verification is restored to its exact pre-step bytes — created files are
removed, overwritten files are put back.

The boundaries are deliberate and worth knowing before you rely on it:

- **Only the failed step is reverted.** Steps that already passed verification
  stay committed; a failed audit is not a transaction rollback across the plan.
- **Only `write_file` can be undone.** `execute_shell`, `run_sandboxed` and
  `schedule_task` have no inverse. Those steps report `not reverted — no undo`
  rather than implying a rollback that did not happen.
- **Large files are not snapshotted.** Above `plan_audit_revert_max_bytes` the
  prior contents are not held in memory, so the write stands and the output says
  so. Declining to revert is acceptable; silently failing to revert is not.

The worst case is bounded by construction: a revert restores the exact bytes that
were there before the step, so a wrong `fail` verdict costs you the step, never
data that predates it.

### What verification costs

`_TurnBudget` attributes tokens to the role that spent them (`main`, or
`subagent:<type>`), and each model-telemetry line carries the same `role` field.
After an audited plan the result reports the share, e.g.
`Verification cost this turn: 22% of tokens (4400 of 20000)`.

This exists so the decision to run auditing is made from your own numbers.
Published figures put auditors at roughly a fifth to a third of harness tokens;
whether that holds for your workload is measurable rather than assumed.

## Tools are data

`tools.txt` is a pipe-delimited registry, not Python. A tool declares a `mode`,
and the mode determines its gating. Adding a row gives you a new tool with
correct permissions automatically — no new security code, and no way to
accidentally add a tool that bypasses the gate.

`TOOL_SPECS` (name → spec) and `TOOLS_DEF` (the JSON schema list sent to the
model) are built together from it. The test suite verifies their names match
exactly, so a registered tool can never be invisible to the model, or vice
versa.

## Security layering

Ordered outermost-first; each layer can only refuse, never grant:

```
1. allowed_paths            — is this path in scope at all?
2. sensitive-file floor     — credentials: refuse read AND write, always
3. shell-startup floor      — refuse writes to .zshrc etc., always
4. write path zones         — blocked / no-prompt / prompt
5. check_permission(mode)   — readonly / full-auto / plan-only
6. shell classifier         — safe inspection vs mutation, sees through sh -c
7. hard git blocks          — push / reset --hard, always
8. SSRF guard               — outbound URLs, including redirects
9. sandbox                  — OS isolation for whatever survived
10. output guards           — secret redaction, untrusted wrapping, leak check
```

Layers 2, 3, 7 are the "always-on floor": no mode and no escalation grant
unlocks them. See [Permissions & Security](03-permissions-and-security.md).

## State on disk

| Path | Contents |
|---|---|
| `~/.agent8088/config.txt` | Settings (`0600`) |
| `~/.agent8088/.env` | Secrets (`0600`) |
| `$AGENT8088_HOME/mcp.json` | User MCP servers (`0600`; defaults to `~/.agent8088/mcp.json`) |
| `.agent8088/mcp.json` | Project MCP servers — override user-level |
| `~/.agent8088/gateway-sessions/` | Per-chat history |
| `~/.agent8088/runtime/` | Sandbox runtime |
| `USER.md` | Persona |

There is no database and no server process. State is plain files you can read
and edit.

## Concurrency

Mostly single-threaded and synchronous — deliberately, since it's an
interactive tool. Two exceptions:

- **MCP client** runs an asyncio loop on a dedicated background thread, with a
  synchronous facade (`MCPRuntime`) over it, so the rest of the engine stays
  sync.
- **Gateway** is async end to end (Slack Socket Mode, `discord.py`), calling
  into the sync engine from the adapter layer.

## Known architectural gaps

Honest list, for anyone extending this:

- **No cross-run cost accounting.** `/usage` reports session tokens and
  `max_turn_cost_usd` bounds a single turn, but nothing aggregates spend across
  runs. `model_telemetry=1` writes per-call records to a local JSONL file; the
  gap is that nothing reads them back for aggregation.
- **`browse_page` is read-only.** It renders and extracts text; it can't click
  or fill forms.
