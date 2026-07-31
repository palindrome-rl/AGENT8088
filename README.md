# Agent 8088

**A local AI agent with fine-tuned tool-calling capabilities**

*Developed by Palindrome Research Labs*

---

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

---

## Overview

Agent 8088 is a local AI agent powered by a fine-tuned Qwen 2.5 14B model, designed for reliable tool calling, multi-turn context retention, and seamless CLI integration. It runs entirely on your machine via Ollama or any OpenAI-compatible endpoint.

### Key Features

- **One-line install** — install on macOS, Linux, and Windows
- **13 model providers** — Ollama, OpenRouter, OpenAI, Anthropic, Gemini, Cerebras, DeepSeek, Groq, Mistral, Moonshot, Qwen, Ollama Cloud, GitHub Copilot
- **Interactive model picker** — fuzzy searchable provider + model selection (InquirerPy)
- **Fallback chains** — automatically switches to backup provider on 429/503 errors
- **Fine-tuned tool calling** — 95% accuracy on function selection
- **Permission layer** — readonly by default, per-action y/n escalation for writes
- **Security layers** — sensitive file protection, network gating, path-based zones
- **Free native sandbox** — OS isolation on macOS, Linux, and Windows; Docker fallback
- **Cross-platform** — Windows (cmd.exe) and Linux/macOS (bash)
- **Rich CLI UI** — live token streaming, ESC interrupt, tool diffs, slash commands
- **Tool alias resolution** — model can call `bash`/`mkdir`/`cat` naturally
- **Tool arg transforms** — `mkdir({path:...})` auto-converts to `execute_shell({command:...})`
- **SkillOpt** — self-improving agent skills via text-space optimization

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
usage: agent8088 [--version] [-h] [--edit] [--uninstall] [--update] [--setup] [--sandbox-setup]

Agent8088 - Local AI Assistant

options:
  -h, --help     show this help and exit
  --version, -V  show version and exit
  --edit         start in edit mode (no per-action permission prompts)
  --uninstall    remove agent8088 install dir + env vars, then exit
  --update       pull latest code + reinstall, then exit
  --setup        run interactive config wizard, then exit
  --sandbox-setup install the free native sandbox runtime

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

- **readonly** (default) — read files, run inspection-only shell commands (`ls`, `cat`, `git status`). Every write/mutation prompts y/n.
- **edit** (`--edit` flag) — everything readonly allows, plus writes within `allowed_paths`. Still forbidden: `git push`, `git reset --hard`, branch deletion.

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
| `get_page_title` | shell | Fetch a webpage title (cross-platform) |
| `calculate` | python_eval | Evaluate a math expression |
| `last_output` | last_output | Get full output from the last tool call |

### Tool Aliases

The model can call tools by natural names — `bash`→`execute_shell`, `cat`→`read_text`, `mkdir`→`execute_shell`, etc. 20+ aliases covering common shell commands.

---

## Repository Structure

```
AGENT8088/
├── src/agent8088/            # Installable package — the ONLY place data files live
│   ├── __init__.py           # Version
│   ├── engine.py             # Core engine (agent loop, tools, permissions)
│   ├── cli.py                # Rich CLI (streaming, slash commands, escalation)
│   ├── providers.py          # Multi-model provider registry (13 providers)
│   ├── config.txt            # Shipped default config (see config lookup below)
│   ├── system.md             # System prompt / skill document
│   ├── tools.txt             # Tool specs
│   ├── agents/               # Sub-agent profiles
│   └── skills_installed/     # Installable skill packages
├── install.sh                # One-line installer (macOS/Linux)
├── install.ps1               # One-line installer (Windows)
├── pyproject.toml            # Package metadata + entry points
├── tests/                    # Permission layer tests
├── docs/                     # Architecture docs + multi-model providers + roadmap
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
