# Agent8088 — Slash Command Reference

Complete inventory of slash commands across all three Agent8088 surfaces.

**Totals: 40 CLI + 8 Gateway + 39 Web UI** (Web UI = CLI minus `exit`/`quit`, which terminate the REPL).

---

## 1. CLI REPL (`src/agent8088/cli.py` — `COMMANDS` dict, line 3972) — 40 commands

### Chat & Agent
| Command | What it does |
|---|---|
| `/help` | Show the command list |
| `/capabilities` | Full self-report: tools, MCP, skills, sub-agents, limits, guardrails |
| `/agents` | List available sub-agent profiles |
| `/agent [name] [task]` | Run a sub-agent (no args → arrow-key picker) |
| `/plan [task]` | Enter plan mode — propose, approve, then it runs |
| `/audit [on\|off]` | Verify each step against real files after it runs |
| `/raw <text>` | One raw model call — shows content, reasoning, tool_calls |

### Tools & Skills
| Command | What it does |
|---|---|
| `/tools` | List every tool with args, mode, description |
| `/tool <name> <args>` | Invoke ONE tool directly (JSON or key=value args) |
| `/skills [name\|enable\|disable]` | Browse a skill or enable/disable for this session |
| `/cli-anything [task]` | Find, run, build, refine, test, or validate an app CLI |
| `/mcp` | List MCP servers, connection state, errors, discovered tools |
| `/mcp reload` | Reconnect MCP servers after config change |
| `/mcp add <name> stdio <command> [args...] [--project]` | Add a local MCP server |
| `/mcp add <name> http <url> [--project]` | Add a Streamable HTTP MCP server |
| `/mcp remove <name> [--project]` | Remove an MCP server |
| `/sandbox [auto\|native\|docker\|local\|setup]` | Show/configure command isolation |

### Model & Vision
| Command | What it does |
|---|---|
| `/model [provider[:model]\|provider model\|setup]` | Show/switch providers or add one |
| `/models [provider\|custom]` | Pick provider/model or connect a custom endpoint |
| `/image <path> [q]` | Analyze a screenshot/diagram with a vision model |
| `/paste [q]` | Analyze an image from the OS clipboard |
| `/temp <float>` | Set sampling temperature |
| `/maxturns <int>` | Set max agent turns |

### Sessions & Memory
| Command | What it does |
|---|---|
| `/new <name>` | Create a named persistent session |
| `/sessions` | List named sessions |
| `/resume <name>` | Load a named session |
| `/reset` | Clear the active session, keep its name |
| `/compact [keep]` | Summarize older turns, retain newest (default 6) |
| `/save <file>` | Save conversation + last trace to JSON |
| `/clear` | Clear conversation context |
| `/memory [on\|off\|search\|add\|forget\|notify\|test\|clear]` | Persistent memory across sessions |

### Diagnostics & Output
| Command | What it does |
|---|---|
| `/status` | Model, context, tool, skill, and session status |
| `/doctor [--fix]` | Check endpoint reachability, auth/config, tools, skills; `--fix` repairs web-search |
| `/dump` | Write a redacted diagnostic bundle for bug reports |
| `/config` | Show active configuration (model, endpoint, paths) |
| `/limits [key value]` | Show/change turn, budget, sub-agent, tool limits (persists) |
| `/history` | Show current conversation |
| `/trace [on\|off]` | Toggle step-by-step JSON trace |
| `/reasoning [on\|off]` | Show/hide model thinking (masked when shown) |
| `/think [on\|off]` | Alias for `/reasoning` |
| `/verbose [on\|off\|full]` | Control tool activity detail |
| `/usage [off\|tokens\|full]` | Control post-turn usage summaries |

### REPL-only
| Command | What it does |
|---|---|
| `/exit`, `/quit` | Leave the REPL |

---

## 2. Gateway (`src/agent8088/gateway/runner.py` — `SLASH_COMMANDS`) — 8 commands

For Slack / Discord / WhatsApp / Telegram / Email chats.

| Command | What it does |
|---|---|
| `/new` | Clear the current session |
| `/stop` | Interrupt the running turn (queued messages cancel) |
| `/help` | Show available commands |
| `/capabilities` | Show tools, MCP servers, skills, limits, and active guardrails |
| `/approve` | Approve a pending action (once/session) |
| `/deny` | Deny a pending action |
| `/mode` | Show or set the permission mode (readonly/full-auto) |
| `/plan` | Enter plan mode and (optionally) propose a plan for the given task |

---

## 3. Web UI (`web/src/components/chat/PromptBar.tsx`) — 39 commands

Mirrors the backend `COMMANDS` (typing `/` in the prompt bar autocompletes them), minus REPL-only commands (`exit`, `quit`, `stop`, `approve`, `deny`) — they either terminate the REPL or expect stdin the web UI doesn't have.

See the CLI table above for the full list.