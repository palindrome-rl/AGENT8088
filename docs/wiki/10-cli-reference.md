# CLI Reference

[← Wiki index](README.md)

## Flags

Verified from the argument parser in `src/agent8088/cli.py`:

```
usage: agent8088 [-h] [--version] [--full-auto]
                 [--mode {readonly,full-auto}] [--uninstall]
                 [--update] [--force] [--setup] [--model-setup] [--sandbox-setup]
                 [--gateway] [--gateway-setup] [--mcp-serve] [--mcp-http]
                 [--mcp-port PORT] [--mcp-host HOST]
```

| Flag | Purpose |
|---|---|
| `-h`, `--help` | Show help and exit |
| `-V`, `--version` | Show version and exit |
| `--mode {readonly,full-auto}` | Permission mode at startup. Plan mode is not settable here — start it with `/plan` |
| `--full-auto` | Start in full-auto (no per-action prompts) |
| `--setup` | Interactive config wizard, then exit |
| `--model-setup` | Configure a model provider profile |
| `--sandbox-setup` | Install the native sandbox runtime |
| `--gateway` | Run the messaging gateway instead of the REPL |
| `--gateway-setup` | Configure gateway channels, then exit |
| `--mcp-serve` | Run as an MCP server |
| `--mcp-http` | Use HTTP transport (with `--mcp-serve`) |
| `--mcp-port PORT` | MCP HTTP port (default `8931`) |
| `--mcp-host HOST` | MCP bind host (default `127.0.0.1`) |
| `--update` | Pull latest code + reinstall, then exit |
| `--force` | With `--update`: discard local changes in the install dir first |
| `--uninstall` | Remove install dir + env vars, then exit |

Run with no flags for the interactive REPL.

## Slash commands

**37 registered commands.** Prefix-matched, so `/mo` offers `/mode`, `/model`,
`/models`.

### Session

| Command | Does |
|---|---|
| `/help` | List all commands |
| `/new [name]` | Start a fresh session, optionally named |
| `/sessions` | List saved named sessions |
| `/resume <name>` | Reload a named session (restores skill state too) |
| `/reset` | Clear the current conversation |
| `/clear` | Clear context |
| `/history` | Show conversation history |
| `/compact [n]` | Summarise older turns, keep the last `n` verbatim |
| `/save <file>` | Export conversation + trace to JSON (mode `0600`) |
| `/status` | Model, mode, tools, skills, token usage |
| `/usage` | Token usage for this session |
| `/exit` | Quit |

### Model

| Command | Does |
|---|---|
| `/model <provider:model>` | Switch provider + model |
| `/model setup` | Add or update a provider profile |
| `/models [provider]` | Fuzzy model picker, fetched live |
| `/temp <float>` | Sampling temperature |
| `/maxturns <int>` | Max agent turns per prompt (same as `/limits max_turns`) |
| `/reasoning` | Toggle reasoning display |
| `/think` | Extended thinking mode |
| `/raw <text>` | One raw model call — content, reasoning, tool_calls |

### Tools and execution

| Command | Does |
|---|---|
| `/tools` | List tools with mode, args, description |
| `/capabilities` | Full self-report: tools, MCP servers, skills, subagents, limits, active guardrails |
| `/tool <name> <json>` | Invoke one tool directly |
| `/plan [task]` | Enter plan mode: propose a plan, approve it, then it runs |
| `/audit [on\|off]` | Show or change step verification; no argument reports the current setting and the last turn's cost |
| `/image <path>` | Attach an image to the next prompt |
| `/agents` | List sub-agent profiles |
| `/agent <type> <task>` | Run a sub-agent directly |
| `/skills` | List skills; `disable`/`enable <name>` |

### Permissions and isolation

| Command | Does |
|---|---|
| `/mode [readonly\|full-auto]` | Show or switch permission mode. Use `/plan` to enter plan mode |
| `/reset`, `/clear` | Discard the conversation — asks first unless `destructive_slash_confirm=0` |
| `/sandbox [auto\|native\|docker\|local\|setup]` | Show, select or install isolation |
| `/search [status\|setup\|stop\|doctor\|use <backend>]` | Show, provision, or pin a web search backend |

### MCP

| Command | Does |
|---|---|
| `/mcp` | Server status + discovered tools |
| `/mcp reload` | Reconnect after editing `mcp.json` — asks first unless `mcp_reload_confirm=0` |
| `/mcp add <name> stdio <cmd> [args...] [--project]` | Add a stdio server |
| `/mcp add <name> http <url> [--project]` | Add an HTTP server |
| `/mcp remove <name> [--project]` | Remove a server |

### Diagnostics

| Command | Does |
|---|---|
| `/limits` | Show every limit: turns, budgets, write caps, sub-agent turns, tool timeouts |
| `/limits <key> <value>` | Change one — **persists to `config.txt`** |
| `/limits subagent <name> <turns>` | Per-profile sub-agent round cap |
| `/limits tool <name> <seconds>` | Per-tool timeout |
| `/config` | Active config + file path |
| `/capabilities` | What the agent can do and which guardrails are in force |
| `/doctor [--fix]` | Environment health check; `--fix` repairs a broken web-search install |
| `/dump` | Write a redacted diagnostic bundle to disk, for sharing in a bug report |
| `/trace [on\|off]` | Toggle JSON trace capture |
| `/verbose` | Toggle verbose output |

### Memory

| Command | Does |
|---|---|
| `/memory` | Status: count, embedder, store size, last call's cost |
| `/memory search <query>` | Run the search and show each leg's rank (words vs meaning) |
| `/memory add <text>` | Store a fact by hand |
| `/memory forget <id>` | Delete one (the short id from `/memory search` works) |
| `/memory notify off\|on\|verbose` | Change what memory prints when it learns something |
| `/memory test` | Run one extraction call and show the raw reply, parsed facts, and elapsed time |
| `/memory clear` | Delete all memories, with confirmation |
| `/memory off` | Stop recalling and learning; keeps what is stored |

See [Memory](16-memory.md) for what each does and when to reach for them.

## Gateway commands

Inside Slack / WhatsApp / Discord / Telegram / Email:

| Command | Does |
|---|---|
| `/new` | Clear the current session |
| `/stop` | Cancel queued messages for this chat |
| `/help` | List available commands |
| `/capabilities` | Tools, MCP servers, skills, limits, active guardrails |
| `/approve` | Approve the pending action (add `session` to hold for the session) |
| `/deny` | Refuse it |

Discord also offers ✅ / ❌ buttons with a fail-closed timeout.

Gateway commands count against `gateway_rate_limit_per_min` like any other
message — otherwise `/help` would be a free channel for flooding the gateway.

## Keyboard

| Key | Does |
|---|---|
| `ESC` | Interrupt the current turn |
| `Ctrl+C` | Cancel input / exit |
| `Tab` | Complete a slash command |

## Environment variables

| Var | Purpose |
|---|---|
| `AGENT8088_CONFIG` | Config file path (`/nonexistent` forces packaged defaults) |
| `AGENT8088_HOME` | Data directory |
| `AGENT8088_PROVIDER` | Active provider |
| `AGENT8088_PERMISSION` | Starting permission mode (`readonly` or `full-auto`; `plan-only` falls back to `readonly`) |
| `AGENT8088_SANDBOX` | Sandbox backend |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Error (or a failing verification script) |
| `2` | Invalid CLI arguments |
