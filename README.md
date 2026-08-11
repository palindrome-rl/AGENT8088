# Agent 8088

**A local AI agent with fine-tuned tool-calling capabilities**

*Developed by Palindrome Research Labs*

---

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## Overview

Agent 8088 is a local AI agent powered by a fine-tuned Qwen 2.5 14B model, designed for reliable tool calling, multi-turn context retention, and seamless CLI integration. It runs entirely on your machine via Ollama or any OpenAI-compatible endpoint.

> 📖 **[Full documentation → `docs/wiki/`](docs/wiki/README.md)** — 15 pages covering
> configuration, the permission model, every tool, MCP in both directions, the
> messaging gateway, architecture, testing and troubleshooting.

### Key Features

- **One-line install** — install on macOS, Linux, and Windows
- **12 model providers** — Ollama, Ollama Cloud, OpenRouter, OpenAI, Gemini, Cerebras, DeepSeek, Groq, Mistral, Moonshot, Qwen, GitHub Copilot — plus custom OpenAI-compatible endpoints and `api_mode=litellm` (which covers Anthropic/Claude directly)
- **Interactive model picker** — fuzzy searchable provider + model selection (InquirerPy)
- **Fallback chains** — automatically switches to backup provider on 429/503 errors
- **Fine-tuned tool calling** — 95% accuracy on function selection
- **3 permission modes** — readonly (default), full-auto, plan-only — switchable at runtime via `/mode`
- **Security layers** — sensitive file protection, network gating, path-based zones, credential blocklist
- **Egress policy** — `allowed_domains` / `blocked_domains` bound which public hosts are reachable, on top of SSRF
- **Outbound secret guard** — a request carrying a configured credential is refused in every mode, including full-auto
- **Resource budgets** — per-request token, cost, and wall-clock ceilings; per-turn write count and size caps
- **Command allowlist** — `allow_commands` restricts shell to an approved set; `deny_commands` still wins
- **Audit trail** — one redacted JSON line per gated decision (`audit_log=1`)
- **Denial circuit breaker** — stops the agent re-proposing a denied action every round
- **Unattended-run policy** — `cron_mode` resolves approval gates for scheduled runs with no operator
- **MCP circuit breaker** — a dead MCP server is not retried every round
- **Gateway rate limiting** — per-user sliding window so one chat user can't starve the queue
- **Capability self-report** — ask the agent what tools, MCP servers, and guardrails it has; `/capabilities`
- **Free native sandbox** — OS isolation on macOS, Linux, and Windows; Docker fallback
- **Cross-platform** — Windows (cmd.exe) and Linux/macOS (bash)
- **Rich CLI UI** — live token streaming, ESC interrupt, tool diffs, slash commands
- **Tool alias resolution** — model can call `bash`/`mkdir`/`cat` naturally
- **Tool arg transforms** — `mkdir({path:...})` auto-converts to `execute_shell({command:...})`
- **SkillOpt** — self-improving agent skills via text-space optimization
- **MCP client** — connect stdio and Streamable HTTP MCP servers as Agent8088 tools
- **MCP server** — expose Agent8088's tools to external AI agents (Claude Code, Codex, Cursor) via stdio or HTTP
- **Messaging gateway** — Slack, WhatsApp, Discord, Email, and Telegram adapters with allowlist + approval prompts
- **Chat-based approvals** — `/approve` + `/deny` in chat; Discord gets interactive ✅/❌ buttons
- **Separate .env key store** — API keys and tokens stored in `~/.agent8088/.env` (0600), not in config.txt

---

## Quick Start

### One-Line Install

** Ubuntu / Linux / macOS / WSL2 / Termux:**
```sh
curl -fsSL https://raw.githubusercontent.com/tayyabimam1/AGENT8088/main/install.sh | bash
```

**Windows (native PowerShell):**
```powershell
iex (irm https://raw.githubusercontent.com/tayyabimam1/AGENT8088/main/install.ps1)
```

The installer:
1. Installs [uv](https://docs.astral.sh/uv/) (Python package manager) if missing
2. Clones the repo and creates an isolated venv
3. Installs agent8088 as a global `agent8088` command
4. Drops a default `config.txt` (localhost Ollama) to `~/.agent8088/`
5. Runs an optional setup wizard to configure your model endpoint

No admin rights required. Works on macOS, Ubuntu, Windows, WSL2, and Termux.

Install the free native sandbox runtime after setup:

```sh
agent8088 --sandbox-setup
```

This uses the open-source Anthropic sandbox runtime. Agent8088 uses Docker
automatically when the native runtime is unavailable, and asks before running
locally when neither backend exists. Native setup needs Node.js 20.11+; Linux
also needs `bubblewrap`, `socat`, and `ripgrep`, macOS needs `ripgrep`, and
Windows shows one UAC prompt to provision its restricted sandbox account.

### Verify

```sh
agent8088 --version
```

### Configure Your Model

The setup wizard uses an interactive fuzzy searchable picker — type to filter providers, arrow keys to navigate, Enter to select:

```sh
agent8088 --setup
```

The wizard prompts for:
1. **Working directory** — where the agent can read/write files (default: `~`)
2. **Provider** — fuzzy search through 12 built-in providers, plus **Custom OpenAI-compatible**
3. **API key** — hidden input for the selected provider
4. **Model** — fetches the provider's available models via `/v1/models` and shows them in a fuzzy picker; custom providers ask for URL, model, and auth/API key
5. **Web search** — pick a backend: SearXNG (auto-provisioned when Docker is present), the bundled keyless `ddgs` fallback, an existing instance URL, or an optional Tavily/Exa API key

Or edit the config file directly:
- **macOS/Linux:** `~/.agent8088/config.txt`
- **Windows:** `%LOCALAPPDATA%\agent8088\config.txt`

```ini
# Active model in provider:model_name format
model=cerebras:gpt-oss-120b

# Provider API key (or set the env var, e.g. CEREBRAS_API_KEY)
provider.cerebras.api_key=csk-...

# Fallback chain (tried on 429/503/connection errors)
fallback_models=groq:llama-3.3-70b-versatile,gemini:gemini-2.0-flash
```

See [Multi-Model Provider Support](docs/multi-model-providers.md) for the full provider list and configuration details.

### Run

```sh
agent8088
```

You'll see the banner with model info, tool count, and the prompt. Type a question or `/help` for commands.

---

## CLI Flags

```
usage: agent8088 [--version] [-h] [--mode MODE] [--edit] [--gateway] [--gateway-setup]
                 [--model-setup] [--uninstall] [--update] [--setup] [--sandbox-setup]

Agent8088 - Local AI Assistant

options:
  -h, --help        show this help and exit
  --version, -V     show version and exit
  --mode MODE        set permission mode: readonly (default), full-auto, or plan-only
  --edit            alias for --mode full-auto (no per-action permission prompts)
  --gateway         run the messaging gateway (Slack/WhatsApp/Discord/Email) instead of REPL
  --gateway-setup   configure gateway channels interactively, then exit
  --mcp-serve      run Agent8088 as an MCP server (expose tools to external AI agents)
  --mcp-http       use HTTP transport for MCP server (with --mcp-serve, default: stdio)
  --mcp-port PORT  MCP server HTTP port (default 8931, with --mcp-http)
  --mcp-host HOST  MCP server bind host (default 127.0.0.1, with --mcp-http)
  --model-setup     configure model provider + API key, then exit
  --uninstall       remove agent8088 install dir + env vars, then exit
  --update          pull latest code + reinstall, then exit
  --setup           run interactive config wizard, then exit
  --sandbox-setup   install the free native sandbox runtime

Run with no flags to start the interactive REPL.
```

---

## REPL Slash Commands

| Command | What it does |
|---|---|
| `/help` | List all commands |
| `/tools` | List loaded tools with args/mode/description |
| `/capabilities` | Full self-report: tools, MCP servers, skills, subagents, limits, active guardrails |
| `/tool <name> <args>` | Invoke one tool directly |
| `/plan [task]` | Enter plan mode: propose a plan, approve it, then it runs |
| `/raw <text>` | One raw model call — shows content + reasoning + tool_calls |
| `/model <provider:model>` | Switch provider + model (e.g. `/model cerebras:gpt-oss-120b`); `/model setup` adds/updates a provider |
| `/models [provider]` | Fuzzy searchable model picker — lists + switches models from active or specified provider |
| `/mode [readonly\|full-auto\|plan-only]` | Show or switch permission mode at runtime |
| `/mcp` | Show MCP server status and discovered tools |
| `/mcp reload` | Reconnect MCP servers after editing configuration |
| `/mcp add <name> stdio <command> [args...] [--project]` | Add a local MCP server |
| `/mcp add <name> http <url> [--project]` | Add a Streamable HTTP MCP server |
| `/mcp remove <name> [--project]` | Remove a configured MCP server |
| `/sandbox [auto\|native\|docker\|local\|setup]` | Show, install, or select command isolation |
| `/search [status\|setup\|stop\|doctor\|use <backend>]` | Show, provision, or pin a web search backend |
| `/config` | Show active config + config file path |
| `/system` | Show the full system prompt |
| `/history` | Show conversation history |
| `/trace [on\|off]` | Toggle JSON trace capture |
| `/temp <float>` | Set sampling temperature |
| `/maxturns <int>` | Set max agent turns |
| `/save <file>` | Save conversation + trace to JSON |
| `/clear` | Clear conversation context |
| `/exit` | Quit |

---

## Configuration

The config file (`config.txt`) is a flat `key=value` file with `#` comments. Key settings:

| Key | Default | Purpose |
|---|---|---|
| `model` | `ollama:qwen14b-tooluse-v3` | Active model in `provider:model_name` format |
| `provider.<name>.api_key` | (env var) | API key for a provider (e.g. `provider.cerebras.api_key=csk-...`) |
| `provider.<name>.base_url` | (built-in) | Override a provider's endpoint URL |
| `fallback_models` | (empty) | Comma-separated fallback chain (e.g. `groq:llama-3.3-70b-versatile,gemini:gemini-2.0-flash`) |
| `timeout_seconds` | `120` | Request timeout |
| `allowed_paths` | `.` | Paths the agent can read/write; `.` is the launch workspace |
| `prompt_paths` | `~` | Writes here show y/n escalation |
| `blocked_paths` | (commented) | Writes here always blocked, even in edit mode |
| `sandbox_backend` | `auto` | Native OS sandbox, then Docker fallback; `local` is explicit opt-in |
| `docker_pull_seconds` | `300` | Budget for pulling a missing container image, separate from any tool's own timeout |
| `plan_audit` | `0` | Verify every mutating `execute_plan` step with the readonly `auditor` sub-agent; a failed verification halts the plan |
| `plan_audit_revert` | `1` | Restore a write that failed verification to its exact pre-step bytes, so only verified state persists |
| `plan_audit_revert_max_bytes` | `1048576` | Files above this are not snapshotted, so they are not reverted (reported, never implied) |
| `sandbox_allowed_domains` | (empty) | Network domains reachable from sandboxed commands |
| `model_telemetry` | `0` | Append local, metadata-only model-call health records |
| `model_telemetry_path` | `<data dir>/model-telemetry.jsonl` | Local path for model telemetry (mode 0600) |
| `search_base_url` | `http://127.0.0.1:8888/search?q=` | Temporary LAN SearXNG URL for web_search; replace before public distribution |
| `web_search_provider` | `searxng` | Temporary LAN deployment pin; replace before public distribution |
| `web_search_results` | `5` | Results per search (max 20) |
| `web_search_no_prompt` | `1` | Temporary LAN opt-in to no-prompt search; replace before public distribution |
| `gateway_permission_mode` | `readonly` | Gateway permission mode: `readonly` (approvals in chat) or `edit` (full-auto) |
| `strict_platform_allowlist` | `1` | Refuse a user id listed under another platform's `*_allowed_users` line |
| `mcp_server_allow_writes` | `0` | Expose `write_file` over `--mcp-serve`. Writes are unattended (MCP has no approval channel) |
| `disabled_tools` | (empty) | Comma-separated built-in tool names to disable (e.g. `browse_page` when MCP Playwright is connected) |

### API Key Storage

API keys and gateway tokens are stored in `~/.agent8088/.env` (file permissions 0600), not in `config.txt`. Config keys like `provider.<name>.api_key_env=OPENROUTER_API_KEY` point to env var names in the `.env` file. On first startup, existing keys in `config.txt` are automatically migrated to `.env`.

### MCP servers

Agent8088 discovers MCP tools at startup from standard `mcp.json` configuration files (using the standard `mcpServers` structure compatible with standard MCP clients):
- `~/.agent8088/mcp.json` for user-level global servers.
- `.agent8088/mcp.json` at the project root for project-specific servers.

A project server definition with the same name overrides the user-level definition. Run `/mcp` to inspect configured servers and `/mcp reload` after editing configuration.

```json
{
  "mcpServers": {
    "supabase": {
      "command": "npx",
      "args": ["-y", "@supabase/mcp-server-supabase@latest", "--project-ref", "YOUR_PROJECT_REF"],
      "env": {
        "SUPABASE_ACCESS_TOKEN": "YOUR_SUPABASE_ACCESS_TOKEN"
      }
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"],
      "env": {"LOG_LEVEL": "warn"},
      "tools": {"include": ["list_directory", "read_file"]}
    },
    "remote-service": {
      "url": "https://mcp.example.com/mcp",
      "bearer_token_env": "COMPANY_MCP_TOKEN",
      "tools": {"exclude": ["delete_*"]}
    }
  }
}
```

MCP tool names are registered dynamically as `mcp_<server>_<tool>`. Stdio server processes receive a minimal OS environment supplemented by any explicit `env` entries. Tools without the server's `readOnlyHint` require normal Agent8088 one-shot approval.

### MCP server mode (expose Agent8088 tools to external agents)

Agent8088 can also act as an MCP **server**, exposing its safe built-in tools to external AI agents like Claude Code, Codex, or Cursor.

**stdio mode** (default — local use):
```bash
agent8088 --mcp-serve
```

MCP client config (e.g. for Claude Code's `.claude/settings.json`):
```json
{
  "mcpServers": {
    "agent8088": {
      "command": "agent8088",
      "args": ["--mcp-serve"]
    }
  }
}
```

**HTTP mode** (localhost only):
```bash
agent8088 --mcp-serve --mcp-http --mcp-port 8931
```

MCP client config (HTTP):
```json
{
  "mcpServers": {
    "agent8088": {
      "url": "http://localhost:8931/mcp"
    }
  }
}
```

**Exposed tools** (curated safe subset — dangerous tools like `execute_shell` and `git_push` are NOT exposed):

| Tool | Description |
|---|---|
| `read_text` | Read a file |
| `calculate` | Evaluate a math expression |
| `web_search` | Search the web — SearXNG by default, Tavily/Exa with a key, keyless `ddgs` fallback |
| `get_page_title` | Fetch a webpage title |
| `last_output` | Get previous tool output |
| `describe_capabilities` | What this server can do, and its active limits and guardrails |

**File writes are opt-in.** MCP has no approval channel — an escalation prompt
would just be an error string the client cannot answer — so the server runs in
full-auto and the default surface above is deliberately read-only. To expose
`write_file`, set `mcp_server_allow_writes=1` in `config.txt`. Writes then run
unattended with no prompt, so narrow `allowed_paths` and set `blocked_paths`
first. The always-on floor still applies either way: sensitive files (`.env`,
`.ssh`, key files) and shell startup files (`.zshrc`, `.bashrc`, `.profile`, …)
are refused regardless of mode, since writing one is code execution on the
user's next shell launch.

Transport: **stdio** (default) or **HTTP** (`--mcp-http`). HTTP is restricted to localhost because this server has no authentication. Use a local client or stdio; remote MCP requires an authenticated proxy that is not included here.

### Environment Variables

| Var | Default | Purpose |
|---|---|---|
| `AGENT8088_CONFIG` | `~/.agent8088/config.txt` | Override config file path |
| `AGENT8088_PERMISSION` | `readonly` | `readonly` or `edit` |
| `AGENT8088_SANDBOX` | (config value) | Temporary `auto`, `native`, `docker`, or `local` override |
| `AGENT8088_HOME` | `~/.agent8088` | Install/data directory |

---

## Permission & Security Layers

### Permission Modes

- **readonly** (default) — read files, run inspection-only shell commands (`ls`, `cat`, `git status`). Every write/mutation prompts y/n. Gateway sends approval prompts to chat (`/approve`, `/deny`).
- **full-auto** (`--edit` or `--mode full-auto`) — everything readonly allows, plus writes within `allowed_paths`. Still forbidden: `git push`, `git reset --hard`, branch deletion, credential paths.
- **plan-only** (`--mode plan-only`, or `/plan`) — reads only. The agent researches, then calls `present_plan` with the plan as markdown; you approve it, the mode changes so the plan runs, and the session returns to its previous mode when the work is done.

Switch modes at runtime with `/mode readonly`, `/mode full-auto`, or `/mode plan-only`.
`/plan [task]` is the shorthand for entering plan mode, optionally with the task.

### Chat-Based Approvals (Gateway)

When the gateway runs in readonly mode, write/shell tools trigger an approval prompt in chat:
- **Discord** — interactive ✅/❌ buttons (Approve Once, Approve Session, Deny)
- **Slack/WhatsApp** — text commands `/approve` (once), `/approve session`, `/deny`
- 300s timeout, fail-closed (auto-deny on timeout)

### Security Layer 1: Sensitive File Protection

Hardcoded blocklist: `.env`, `config.txt`, `id_rsa`, `*.pem`, `*.key`, `*_KEY*`, `*_SECRET*`, `*_TOKEN*`. `allowed_sensitive_files=` exempts only exact paths (relative paths resolve from the workspace).

### Security Layer 2: Network Access Control

`get_page_title` prompts y/n on every request. `web_search` does too by default.
Set `web_search_no_prompt=1` only with `web_search_provider=searxng` and a
loopback or explicitly allowlisted private-LAN `search_base_url` (including an
SSH tunnel). This cannot fall back to a public provider, and credentials,
private keys, direct personal identifiers, and queries over 500 characters
remain blocked.

Every web search backend runs the same egress/SSRF/outbound-secret checks before
each request. That includes `ddgs`, whose library owns its own HTTP client: its
fixed upstream hosts are checked against the egress policy *before* the library
is invoked, and it **fails closed** rather than bypassing an `allowed_domains`
policy. A guard denial never falls through to another backend — that would route
around a policy decision rather than an outage.

SSRF protection refuses private, loopback, link-local, and cloud-metadata
addresses on every outbound path, including redirects.

On top of that, the egress policy bounds which **public** hosts are reachable:

```ini
blocked_domains=pastebin.com,transfer.sh   # never reachable; wins over allow
allowed_domains=api.github.com             # if set, the ONLY reachable hosts
```

Matching is dot-anchored, so `example.com` covers `docs.example.com` but not
`evilexample.com`. The policy is checked before the SSRF DNS lookup, so a
rejected host is never even resolved.

### Security Layer 3: Path-Based Write Zones

Three-tier zone system: `no_prompt_paths` (auto-approved), `prompt_paths` (y/n), `blocked_paths` (always denied).

### Security Layer 4: Outbound Secret Guard

Every outbound URL and argument set is scanned for configured secret values. A
match is refused outright — **no permission mode unlocks this, including
full-auto**, and there is no escalation prompt. Secret redaction protects what
comes back from a tool; this protects what goes out.

### Security Layer 5: Resource Budgets and Blast Radius

`max_turns` bounds how many *rounds* a request takes. These bound what those
rounds may consume. All default to `0` (disabled):

```ini
max_turn_tokens=60000        # tokens per request (input + output)
max_turn_seconds=300         # wall clock per request
max_turn_cost_usd=0.50       # spend per request (needs cost_per_1k_* keys)
max_writes_per_turn=20       # files one request may write
max_write_bytes=5242880      # bytes per single write
```

Subagents inherit the parent's budget — a fresh one per subagent would be a free
bypass. When a budget trips, the request returns its partial result plus the name
of the key to raise.

### Security Layer 6: Command Allowlist

`deny_commands` only stops what you thought of; `allow_commands` stops everything
you did not:

```ini
allow_commands=git status,git diff,ls*,pytest*
```

Enforced at the always-on floor, so an unlisted command is not escalatable.
`deny_commands` wins over `allow_commands`, and neither can re-enable the
unrecoverable floor (`rm -rf /`, `mkfs`, `curl | sh`).

### Security Layer 7: Approval Policy

```ini
denial_breaker_threshold=3         # stop after N consecutive denials; 0 disables
cron_mode=deny                     # unattended runs: deny (default) | approve
destructive_slash_confirm=1        # /reset and /clear ask first
mcp_reload_confirm=1               # /mcp reload asks first
```

There is no separate "approval mode" knob — `--mode` already decides what is
gated, and a second axis that could also wave a gate through would let
`readonly` silently behave like `full-auto`.

### Security Layer 8: Audit Trail

```ini
audit_log=1
```

Appends one redacted JSON line per gated decision (`allowed` / `blocked` /
`denied`) at mode 0600. Recommended for any gateway deployment — it is the only
durable record of who asked for what and what was refused. Rotation is external;
point `audit_log_path` at a file your `logrotate` handles.

### Local model telemetry

```ini
model_telemetry=1
```

Writes local JSONL metadata for each model call: provider/model, latency, token
and cost estimates, finish reason, and sanitized error status. It never records
prompts, responses, tool arguments, paths, or credentials; it sends nothing to
an external service. The default path is mode 0600 under Agent8088's data dir.

### Command Sandbox

Shell tools, structured Git tools, sandboxed Python, and subagent tool calls use
the same backend. `auto` prefers native OS isolation and falls back to Docker.
Sandboxed commands have no network unless `sandbox_allowed_domains` is set.
When neither backend is available, Agent8088 asks before running locally.

---

## Tools

| Tool | Mode | Description |
|---|---|---|
| `execute_shell` | shell | Run a shell command |
| `write_file` | write_text | Write content to a file |
| `read_text` | read_text | Read text from a file |
| `web_search` | search | Routes to the configured backend (SearXNG / Tavily / Exa / ddgs) with automatic fallback |
| `get_page_title` | shell | Fetch a webpage title (cross-platform) |
| `browse_page` | browser | Load a web page in a headless browser |
| `calculate` | python_eval | Evaluate a math expression |
| `last_output` | last_output | Get full output from the last tool call |
| `describe_capabilities` | introspect | Report own tools, MCP servers, skills, subagents, mode, sandbox, and active guardrails |
| `present_plan` | plan | Present a plan as markdown for the user to approve (plan mode) |
| `execute_plan` | plan | Run an already-decided sequence of tool calls, verified step by step |
| `spawn_subagent` | subagent | Delegate a task to an independent sub-agent |
| `run_sandboxed` | docker | Run Python with OS isolation |
| `schedule_task` | cron | Schedule periodic tasks (cron) |
| `git_status` | shell | Show git status and branch |
| `git_diff` | shell | Show git diff |
| `git_log` | shell | Show recent commits |
| `git_clone` | shell | Clone a repository |
| `git_commit` | shell | Stage and commit changes |
| `git_push` | shell | Push current branch to origin |
| `git_create_pr` | shell | Open a pull request via `gh` |
| MCP tools (dynamic) | mcp | Discovered from connected MCP servers, registered as `mcp_<server>_<tool>` |

### Tool Aliases

The model can call tools by natural names — `bash`→`execute_shell`, `cat`→`read_text`, `mkdir`→`execute_shell`, etc. 20+ aliases covering common shell commands.

---

## Repository Structure

```
AGENT8088/
├── src/agent8088/            # Installable package — the ONLY place data files live
│   ├── __init__.py           # Version
│   ├── engine.py             # Core engine (agent loop, tools, permissions, MCP)
│   ├── cli.py                # Rich CLI (streaming, slash commands, escalation, gateway setup)
│   ├── providers.py          # Multi-model provider registry (13 providers)
│   ├── mcp.py                # MCP client runtime (connects to external MCP servers)
│   ├── config.txt            # Shipped default config (see config lookup below)
│   ├── system.md             # System prompt / skill document
│   ├── tools.txt             # Tool specs
│   ├── agents/               # Sub-agent profiles
│   ├── skills_installed/     # Installable skill packages
│   └── gateway/              # Messaging gateway (Slack, WhatsApp, Discord, Email)
│       ├── runner.py          # Gateway runner (session, approvals, slash commands)
│       ├── agent_bridge.py    # Bridge between gateway and engine
│       ├── auth.py            # Allowlist + WhatsApp LID resolution
│       ├── session.py         # Per-chat JSON session store
│       └── platforms/        # Channel adapters
│           ├── base.py        # BaseChannelAdapter ABC
│           ├── slack.py       # Slack adapter (Socket Mode)
│           ├── discord.py     # Discord adapter (discord.py + approval buttons)
│           ├── whatsapp.py    # WhatsApp adapter (Baileys bridge)
│           └── email.py       # Email adapter (IMAP/SMTP, stdlib only)
├── tests/                    # Test suite
│   ├── test_permission.py    # Permission layer tests
│   ├── test_cli_setup.py     # CLI setup wizard tests
│   ├── test_env_key_store.py # .env key store tests
│   ├── test_mcp.py           # MCP client tests
│   └── gateway/              # Gateway adapter tests
├── .agent8088/               # Project-scoped MCP config (mcp.json)
├── install.sh                # One-line installer (macOS/Linux)
├── install.ps1               # One-line installer (Windows)
├── pyproject.toml            # Package metadata + entry points
├── MCP_FEATURES.md           # MCP client documentation
├── docs/                     # Architecture docs + specs + capability comparison
├── research/                 # Non-runtime: SkillOpt, benchmarks, training
└── scripts/                  # One-off repo ops
```

### Where data files live

`tools.txt`, `system.md`, `config.txt`, `agents/`, and `skills_installed/` live **only**
under `src/agent8088/`. That is where the engine loads them from (`APP_DIR` is the package
directory) and the only copy shipped in the wheel. Do not add copies at the repo root —
they are never read, and edits to them silently do nothing.

Config is resolved in this order, first match wins:

| Order | Location | Purpose |
|---|---|---|
| 1 | `$AGENT8088_CONFIG` | Explicit override (used by the test suite) |
| 2 | `~/.agent8088/config.txt` | **Your** settings — survives `--update` |
| 3 | `src/agent8088/config.txt` | Shipped defaults / template |

---

## SkillOpt — Self-Improving Agent Skills

Agent8088 includes **SkillOpt**, a text-space optimization system that improves the agent's skill document (`system.md`) without touching model weights. Based on [arXiv:2605.23904](https://arxiv.org/abs/2605.23904).

### How It Works

1. **Rollout** — Run benchmark suite, capture successes/failures
2. **Reflect** — Optimizer model proposes atomic edits to the skill document
3. **Validate** — Run benchmark with edited skill; accept only if score improves
4. **Repeat** — Cosine-decaying textual learning rate over N epochs

### Usage

```bash
python3 research/skillopt.py                # Run full optimization (4 epochs)
python3 research/skillopt.py --epochs 6     # Custom epochs
python3 research/skillopt.py --dry-run      # Preview edits without applying
python3 research/skillopt.py --report       # View optimization history
python3 research/skillopt.py --restore      # Restore pre-optimization skill
```

---

## Development

### Setup

```bash
git clone https://github.com/tayyabimam1/AGENT8088.git
cd AGENT8088
uv sync
```

### Run Tests

```bash
uv run pytest tests/test_permission.py -v
```

### Build & Install Locally

```bash
uv build
uv tool install --force dist/agent8088-*.whl
agent8088 --version
```

### Update an Existing Install

```bash
agent8088 --update
```

---

## Troubleshooting

**`agent8088: command not found`**
- Open a NEW terminal (PATH was updated by the installer).
- macOS/Linux: ensure `~/.local/bin` is on PATH (`uv tool update-shell`).

**`Connection error.`**
- The model endpoint isn't reachable. Run `agent8088 --setup` to pick a different provider, or use `/models` in the REPL to switch providers at runtime.

**`429 Rate limit` / `Quota exceeded`**
- Your provider's rate limit is hit. Set `fallback_models` in config to automatically switch to a backup provider on 429 errors.

**`Tools: 0 loaded`**
- `tools.txt` is missing from the install. Reinstall: `agent8088 --update`.

**ESC-to-interrupt doesn't work**
- Unix-only feature (uses `termios`). On Windows, use Ctrl-C.

**Git login prompt during install**
- The installer suppresses credential prompts (`GIT_TERMINAL_PROMPT=0`). If you still see one, ensure the repo URL in the script points to the public repo.

**MCP server not connecting**
- Run `/mcp` to see per-server connection state and errors.
- After editing `mcp.json`, run `/mcp reload`.
- Ensure the stdio server command is installed and on PATH (e.g. `npx` needs Node.js 18+).
- For HTTP servers, ensure the `bearer_token_env` environment variable is set.

**Gateway not starting**
- Run `agent8088 --gateway-setup` to enable a channel (only one channel active at a time).
- Check that tokens are in `~/.agent8088/.env` (not config.txt).
- Run `/mcp` and `/tools` to verify tools are discovered.

**Gateway approval not working**
- The gateway runs in readonly mode by default. Set `gateway_permission_mode=edit` in config.txt to disable approvals.
- For chat-based approvals: send `/approve` (once), `/approve session`, or `/deny` in the chat.
- Discord gets interactive ✅/❌ buttons.

**Email adapter not receiving messages**
- Run `agent8088 --gateway-setup` and select Email to configure.
- Ensure `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `EMAIL_SMTP_HOST`, and `EMAIL_IMAP_HOST` are set in `~/.agent8088/.env`.
- For Gmail: enable 2FA and create an App Password (not your regular password).
- Only emails from `email_allowed_users` addresses are processed — all others are silently dropped.
- IMAP host should start with `imap.` (e.g. `imap.gmail.com`), not `smtp.`.
- SPF/DKIM/DMARC verification is enabled by default and fails closed. Set `email_verify_sender=0` only for a trusted relay that does not provide authentication results.

---

## License

[MIT](LICENSE)

---

## Citation

```bibtex
@software{agent8088,
  title = {Agent 8088: Fine-Tuned AI Agent for Tool Calling},
  author = {Palindrome Research Labs},
  year = {2026},
  url = {https://github.com/tayyabimam1/AGENT8088}
}
```
