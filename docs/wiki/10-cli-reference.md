# CLI Reference

[← Wiki index](README.md)

## Flags

Verified from the argument parser in `src/agent8088/cli.py`:

```
usage: agent8088 [-h] [--version] [--full-auto]
                 [--mode {readonly,full-auto}]
                 [--uninstall] [--workspace] [--all] [--yes]
                 [--non-interactive] [--dry-run]
                 [--update] [--force] [--setup] [--doctor] [--model-setup] [--sandbox-setup] [--memory-setup]
                 [--libreoffice-setup]
                 [--gateway] [--gateway-setup] [--mcp-serve] [--mcp-http]
                 [--mcp-port PORT] [--mcp-host HOST]
                 [--web] [--web-port PORT] [--web-host HOST] [--web-dev]
                 [--prompt-file PATH]
                 [--logs [MODE]] [-n LIMIT] [--level LEVEL] [--subsystem SUBSYSTEM] [--json]
```

| Flag | Purpose |
|---|---|
| `-h`, `--help` | Show help and exit |
| `-V`, `--version` | Show version and exit |
| `--mode {readonly,full-auto}` | Permission mode at startup. Plan mode is not settable here — start it with `/plan` |
| `--full-auto` | Start in full-auto (no per-action prompts) |
| `--setup` | Interactive config wizard, then exit |
| `--doctor` | Check provider, model, configuration and optional capabilities without entering the REPL; prints fixes and exits non-zero on a failing check |
| `--model-setup` | Configure a model provider profile |
| `--sandbox-setup` | Install the native sandbox runtime |
| `--memory-setup` | Install the mem0 memory backend deps and set `memory_engine=mem0`; switch back with `/memory engine native` |
| `--libreoffice-setup` | Install LibreOffice (via winget/brew/apt-get/dnf/pacman, whichever is available) for document conversion, legacy Office formats, and formula recalculation |
| `--gateway` | Run the messaging gateway instead of the REPL |
| `--gateway-setup` | Configure gateway channels, then exit |
| `--mcp-serve` | Run as an MCP server |
| `--mcp-http` | Use HTTP transport (with `--mcp-serve`) |
| `--mcp-port PORT` | MCP HTTP port (default `8931`) |
| `--mcp-host HOST` | MCP bind host (default `127.0.0.1`) |
| `--web` | Launch the web UI instead of the terminal REPL |
| `--web-port PORT` | Web UI server port (default `8180`) |
| `--web-host HOST` | Web UI bind host (default `127.0.0.1`); loopback only |
| `--web-dev` | Run the web backend without serving built frontend files; use with Vite |
| `--prompt-file PATH` | Run one headless turn using the entire UTF-8 file as the task, then exit. Implies full-auto: use only in a trusted, isolated environment; the benchmark adapter supplies a disposable task container. |
| `--logs [MODE]` | Print the operational log; pass `follow` to tail it in real time. Bare `--logs` prints the last `--limit` lines and exits |
| `-n`, `--limit LIMIT` | With `--logs`: number of lines to print (default 50) |
| `--level LEVEL` | With `--logs`: filter by level (`DEBUG`\|`INFO`\|`WARNING`\|`ERROR`) |
| `--subsystem SUBSYSTEM` | With `--logs`: substring filter on subsystem name |
| `--json` | With `--logs`: emit raw JSONL instead of the human-formatted view |
| `--update` | Update from the public `AGENT8088-v1.2` branch and reinstall, then exit |
| `--force` | With `--update`: discard local changes in the install dir first |
| `--uninstall` | Remove the install dir, shim, PATH/config lines, and crontab/scheduled-task entries, then exit. Trace logs and the WhatsApp session dir are kept unless `--workspace`/`--all` is also passed |
| `--workspace` | With `--uninstall`: also remove trace logs and the WhatsApp session directory |
| `--all` | With `--uninstall`: shorthand for `--workspace` |
| `--yes` | With `--uninstall`: skip the confirmation prompt |
| `--non-interactive` | With `--uninstall`: never prompt; requires `--yes` |
| `--dry-run` | With `--uninstall`: print what would be removed, remove nothing |

Run with no flags for the interactive REPL.

## Web UI

The web UI is the browser interface to the same Agent8088 engine, sessions,
tools, permissions, and configuration used by the CLI.

### Production

Launch the server from the repository root (it builds the frontend when needed):

```sh
agent8088 --web
```

Open `http://127.0.0.1:8180`.

Use the web flags to change the server settings:

```sh
agent8088 --web --web-port 3000
```

The web UI binds to loopback only, and a non-loopback `--web-host` is refused.
Every endpoint is unauthenticated and `POST /api/tool/{name}` runs any tool in
the registry, so exposing the port is equivalent to handing out a shell. To
reach it from another machine, forward the port over SSH:

```sh
ssh -N -L 8180:127.0.0.1:8180 <the-machine-running-agent8088>
```

### Development

Run the backend and Vite together:

```sh
uv run agent8088 --web
```

Open `http://127.0.0.1:5180`. Vite proxies `/api` and `/ws` to the backend.

### What `--uninstall` does and doesn't remove

By default `--uninstall` removes everything the installer created: the
install directory (venv, bundled uv/Node, sandbox runtime), the `agent8088`
command shim, the `PATH` and `AGENT8088_CONFIG` lines it added to shell rc
files (or the Windows user-environment `PATH` entries for the bundled
Git/Node on Windows), any crontab entries or Windows Task Scheduler entries a
`cron_mode` schedule registered, and the SearXNG Docker container from
`/search setup` if one exists — it runs with `--restart unless-stopped`, so
Docker itself would otherwise keep it running (and restart it on reboot)
indefinitely, since deleting the install directory doesn't touch a running
container.

Two things are deliberately **not** touched:

- **Trace logs and the WhatsApp session directory** — these are your data,
  not installer residue, so they're kept by default. Pass `--workspace` (or
  `--all`) to remove them too.
- **A pre-existing shared Playwright browser cache** (`~/.cache/ms-playwright`
  on Linux, `~/Library/Caches/ms-playwright` on macOS,
  `%LOCALAPPDATA%\ms-playwright` on Windows) — other Playwright-based tools on
  the same machine can share that cache, so it's never auto-deleted. A fresh
  install now downloads Chromium into `$AGENT8088_HOME/playwright-browsers`
  instead, so this only applies to installs from before that change; uninstall
  prints the exact manual command to remove the shared cache yourself if one
  is found.

```
agent8088 --uninstall --dry-run              # preview only, nothing removed
agent8088 --uninstall --all --yes --non-interactive   # fully unattended, full removal
```

## Slash commands

**45 registered commands** (`COMMANDS` in `src/agent8088/cli.py`). Prefix-matched,
so `/mo` offers `/mode`, `/model`, `/models`. A mistyped command names the
nearest real one: `/seatch` → `unknown command: /seatch — did you mean /search?`.

The agent knows this list too, so "what does /local do?" is answered from it
rather than guessed (see [Tools](04-tools.md)).

Pasting a bare file path into the prompt — nothing else on the line — reads it
immediately: images go to a vision model, documents are extracted to text.
Unlike a tool call the model makes on its own, this works outside
`allowed_paths`, because it's a path the user personally typed. The
sensitive-file floor still applies unconditionally.

### Session

| Command | Does |
|---|---|
| `/help` | List all commands |
| `/new [name]` | Start a fresh session, optionally named |
| `/sessions` | List saved named sessions |
| `/resume <name>` | Reload a named session (restores skill state too) |
| `/reset` | Clear the current conversation |
| `/history` | Show conversation history |
| `/compact [n]` | Summarise older turns, keep the last `n` verbatim |
| `/save <file>` | Export conversation + trace to JSON (mode `0600`) |
| `/status` | Model, mode, tools, skills, token usage; `Limited` rows for anything on a fallback ([reduced modes](13-troubleshooting.md#reduced-modes-and-fallbacks)) |
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
| `/tool-selection [hybrid\|full\|auto]` | Show or set how native tool schemas are narrowed per request — see [Tool-calling compatibility](05-model-providers.md#tool-calling-compatibility) |
| `/raw <text>` | One raw model call — content, reasoning, tool_calls |
| `/fusion setup` | Interactively pick a default panel and judge (checkbox pickers), saved to `config.txt` |
| `/fusion <query>` | Ask the panel in parallel; a blind judge picks the best answer. `--panel p:m,...`/`--judge p:m` flags override the saved config for one call |

### Tools and execution

| Command | Does |
|---|---|
| `/tools` | List tools with mode, args, description |
| `/capabilities` | Full self-report: tools, MCP servers, skills, subagents, limits, active guardrails |
| `/tool <name> <json>` | Invoke one tool directly |
| `/plan [task]` | Enter plan mode: propose a plan, approve it, then it runs |
| `/audit [on\|off]` | Show or change step verification; no argument reports the current setting and the last turn's cost |
| `/image <path> [question]` | Analyze an image with a vision-capable model |
| `/agents` | List sub-agent profiles, with source, model, and the active provider's models |
| `/agents models` | List every model the active provider offers |
| `/agents new [name]` | Create a custom sub-agent profile interactively |
| `/agents edit <name>` | Open a custom profile in `$EDITOR` |
| `/agents delete <name>` | Delete a custom profile (bundled ones are refused) |
| `/agent <type> <task>` | Run a sub-agent directly; the prompt stays on it until `/quit` |
| `/skills` | List skills; `disable`/`enable <name>` |
| `/cli-anything [task]` | Show integration status, or route a task through the CLI-Anything skill |
| `/browser [status\|close\|reset\|stop]` | Show the headless browser session state, or close it and clear its temp files |
| `/local [list\|available <query>\|pull <name>\|remove <name>]` | Bare form probes hardware and recommends local Ollama models; the CLI counterpart to the `local_models`-mode tools |
| `/task [list\|start <goal>\|resume <id>\|end <id>\|output <id>]` | Start, resume, or inspect a restart-safe model task backed by `task_runtime.TaskStore`. Bare or `list` shows stored tasks |

### Permissions and isolation

| Command | Does |
|---|---|
| `/mode [readonly\|full-auto]` | Show or switch permission mode. Use `/plan` to enter plan mode |
| `/reset` | Discard the conversation — asks first unless `destructive_slash_confirm=0` |
| `/sandbox [auto\|native\|docker\|setup]` | Show, select or install isolation |
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
| `/limits provider <name> <key> <value>` | Per-provider token limit (`context_window` or `max_completion_tokens`) |
| `/schedule list` | Show all scheduled recurring tasks (displays: #, cron schedule, task text) |
| `/schedule add <cron> <task>` | Add a recurring task; cron is 5 fields (minute hour day month weekday), e.g. `/schedule add "0 9 * * *" check disk space` |
| `/schedule remove <index-or-task>` | Remove by the # from `/schedule list`, or by exact task text |
| `/config` | Active config + file path |
| `/capabilities` | What the agent can do and which guardrails are in force |
| `/cost [on\|off\|<task_id>]` | Local telemetry summary of model cost/usage; `on`/`off` toggles recording, persisted to `config.txt` |
| `/review` | Bare: list stored reviews. With arguments: launch `review_code` from the CLI — same tool the model calls, see [`review_code`](04-tools.md#review_code) |
| `/doctor [--fix]` | Environment health check, incl. a row per [reduced mode](13-troubleshooting.md#reduced-modes-and-fallbacks); `--fix` repairs a broken web-search install |
| `/dump` | Write a redacted diagnostic bundle to disk, for sharing in a bug report |
| `/trace [on\|off]` | Toggle JSON trace capture |
| `/verbose` | Toggle verbose output |

### Memory

| Command | Does |
|---|---|
| `/memory` | Status: count, embedder, store size, last call's cost |
| `/memory engine [native\|mem0]` | Show the active memory engine, or switch it live — persists to `config.txt`, never migrates or deletes either store |
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
| `AGENT8088_CLI_ANYTHING_HOME` | Optional override for the isolated CLI-Anything runtime directory |
| `AGENT8088_PROVIDER` | Active provider |
| `AGENT8088_PERMISSION` | Starting permission mode (`readonly` or `full-auto`; `plan-only` falls back to `readonly`) |
| `AGENT8088_SANDBOX` | Sandbox backend |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Error (or a failing verification script) |
| `2` | Invalid CLI arguments |
