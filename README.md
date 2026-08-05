# Agent 8088

**A local AI agent with fine-tuned tool-calling capabilities**

*Developed by Palindrome Research Labs*

---

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## Overview

Agent 8088 is a local AI agent powered by a fine-tuned Qwen 2.5 14B model, designed for reliable tool calling, multi-turn context retention, and seamless CLI integration. It runs entirely on your machine via Ollama or any OpenAI-compatible endpoint.

### Key Features

- **One-line install** — install on macOS, Linux, and Windows
- **13 model providers** — Ollama, OpenRouter, OpenAI, Anthropic, Gemini, Cerebras, DeepSeek, Groq, Mistral, Moonshot, Qwen, Ollama Cloud, GitHub Copilot
- **Interactive model picker** — fuzzy searchable provider + model selection (InquirerPy)
- **Fallback chains** — automatically switches to backup provider on 429/503 errors
- **Fine-tuned tool calling** — 95% accuracy on function selection
- **3 permission modes** — readonly (default), full-auto, plan-only — switchable at runtime via `/mode`
- **Security layers** — sensitive file protection, network gating, path-based zones, credential blocklist
- **Free native sandbox** — OS isolation on macOS, Linux, and Windows; Docker fallback
- **Cross-platform** — Windows (cmd.exe) and Linux/macOS (bash)
- **Rich CLI UI** — live token streaming, ESC interrupt, tool diffs, slash commands
- **Tool alias resolution** — model can call `bash`/`mkdir`/`cat` naturally
- **Tool arg transforms** — `mkdir({path:...})` auto-converts to `execute_shell({command:...})`
- **SkillOpt** — self-improving agent skills via text-space optimization
- **MCP client** — connect stdio and Streamable HTTP MCP servers as Agent8088 tools
- **Messaging gateway** — Slack, WhatsApp, and Discord adapters with DM + @mention support
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
2. **Provider** — fuzzy search through 13 built-in providers, plus **Custom OpenAI-compatible**
3. **API key** — hidden input for the selected provider
4. **Model** — fetches the provider's available models via `/v1/models` and shows them in a fuzzy picker; custom providers ask for URL, model, and auth/API key
5. **Web search URL** — optional SearXNG endpoint

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
  --gateway         run the messaging gateway (Slack/WhatsApp/Discord) instead of REPL
  --gateway-setup   configure gateway channels interactively, then exit
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
| `/tool <name> <args>` | Invoke one tool directly |
| `/plan <steps>` | Run the plan-executor (multi-step) |
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
| `allowed_paths` | `~` | Paths the agent can read/write |
| `prompt_paths` | `~` | Writes here show y/n escalation |
| `blocked_paths` | (commented) | Writes here always blocked, even in edit mode |
| `sandbox_backend` | `auto` | Native OS sandbox, then Docker fallback; `local` is explicit opt-in |
| `sandbox_allowed_domains` | (empty) | Network domains reachable from sandboxed commands |
| `search_base_url` | (commented) | SearXNG URL for web_search (ends at `q=`) |
| `gateway_permission_mode` | `readonly` | Gateway permission mode: `readonly` (approvals in chat) or `edit` (full-auto) |
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
- **plan-only** (`--mode plan-only`) — only `execute_plan` runs; direct tools blocked. Forces the model to plan first, user approves the plan, then steps run with a temporary grant.

Switch modes at runtime with `/mode readonly`, `/mode full-auto`, or `/mode plan-only`.

### Chat-Based Approvals (Gateway)

When the gateway runs in readonly mode, write/shell tools trigger an approval prompt in chat:
- **Discord** — interactive ✅/❌ buttons (Approve Once, Approve Session, Deny)
- **Slack/WhatsApp** — text commands `/approve` (once), `/approve session`, `/deny`
- 300s timeout, fail-closed (auto-deny on timeout)

### Security Layer 1: Sensitive File Protection

Hardcoded blocklist: `.env`, `config.txt`, `id_rsa`, `*.pem`, `*.key`, `*_KEY*`, `*_SECRET*`, `*_TOKEN*`. Override with `allowed_sensitive_files=` in config.

### Security Layer 2: Network Access Control

`web_search` and `get_page_title` prompt y/n on every request. No config needed.

### Security Layer 3: Path-Based Write Zones

Three-tier zone system: `no_prompt_paths` (auto-approved), `prompt_paths` (y/n), `blocked_paths` (always denied).

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
| `web_search` | http_get | Search the web (SearXNG) |
| `web_search_tavily` | http_post | Search via Tavily (agent-optimized) |
| `web_search_exa` | http_post | Search via Exa (semantic/neural) |
| `get_page_title` | shell | Fetch a webpage title (cross-platform) |
| `browse_page` | browser | Load a web page in a headless browser |
| `calculate` | python_eval | Evaluate a math expression |
| `last_output` | last_output | Get full output from the last tool call |
| `execute_plan` | plan | Execute a multi-step plan (plan-only mode) |
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
│   └── gateway/              # Messaging gateway (Slack, WhatsApp, Discord)
│       ├── runner.py          # Gateway runner (session, approvals, slash commands)
│       ├── agent_bridge.py    # Bridge between gateway and engine
│       ├── auth.py            # Allowlist + WhatsApp LID resolution
│       ├── session.py         # Per-chat JSON session store
│       └── platforms/        # Channel adapters
│           ├── base.py        # BaseChannelAdapter ABC
│           ├── slack.py       # Slack adapter (Socket Mode)
│           ├── discord.py     # Discord adapter (discord.py + approval buttons)
│           └── whatsapp.py    # WhatsApp adapter (Baileys bridge)
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
