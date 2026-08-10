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
| `gateway/platforms/*.py` | Slack / WhatsApp / Discord adapters |
| `tools.txt` | Tool registry (data, not code) |
| `system.md` | Base system prompt |
| `config.txt` | Default settings |

## The agent loop

Per user turn, `run_agent()` loops up to `max_turns`:

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

If the model backend errors mid-turn, the loop returns the best output it has
rather than crashing the session.

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
| `~/.agent8088/mcp.json` | User MCP servers (`0600`) |
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

- **No persistent memory.** `USER.md` is read, never written. There's no
  cross-session recall — unlike Hermes' layered memory or Letta's tiered
  core/recall/archival model.
- **No audit trail.** One `_log.info` per tool call; not a structured,
  queryable record of what ran and who approved it.
- **No token/cost accounting.** `/usage` reports session tokens, but nothing
  tracks spend across runs.
- **`browse_page` is read-only.** It renders and extracts text; it can't click
  or fill forms.
