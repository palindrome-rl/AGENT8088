<p align="center">
  <img src="assets/palindrome-research-labs-agent8088.png" alt="Palindrome Research Labs" width="420">
  <br>
  <img src="assets/agent8088-wordmark-readme.png" alt="Agent8088" width="540">
</p>

<p align="center">
  <strong>A local-first AI agent that can use tools, work across your chats, and ask before it acts.</strong>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-it-does">Features</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
  <a href="https://github.com/palindrome-rl/AGENT8088/tree/main"><img src="https://img.shields.io/badge/branch-main-18a0fb" alt="Main branch"></a>
</p>

Agent8088 is a local-first agent for real work: it reads files, runs tools, researches the web, and can make changes only within a permission system you control. Run it against local Ollama or a hosted OpenAI-compatible model, use it from the terminal or a messaging gateway, and extend it with MCP servers, skills, and focused sub-agents.

---

## What it does

| Capability | What it gives you |
| --- | --- |
| **Use the model you want** | 12 built-in provider profiles, local Ollama, OpenRouter, and custom OpenAI-compatible endpoints. Configure fallback models for retryable provider failures. |
| **Work safely by default** | `readonly` is the default. One-time approvals, path zones, credential protection, SSRF and egress controls, command allowlists, and an audit trail are enforced in code. |
| **Plan before changing things** | `/plan` lets the agent investigate first, present a plan for approval, then carry it out. Optional audits use a read-only sub-agent to verify mutating work. |
| **Delegate without losing context** | Five restricted sub-agent profiles handle exploration, research, coding, verification, and general-purpose work in separate runs. |
| **Use tools without lock-in** | 21 built-in tools for files, shell, web research, browser access, scheduling, Git, sandboxed code, and more. Connect external MCP servers or expose Agent8088's safe tools to Codex, Claude Code, or Cursor. |
| **Remember across sessions** | Durable facts about you and your projects are learned from finished turns and recalled automatically, using hybrid keyword + semantic search over a local SQLite store. Nothing leaves your machine. |
| **Stay in your workflow** | Use the interactive CLI or run a gateway for Slack, Discord, WhatsApp, Telegram, and email. Sessions and approvals follow the same engine and permission layer. |
| **Run contained commands** | Native OS sandboxing is preferred, with Docker as a fallback. Network access from sandboxed commands is off unless you allow it. |
| **Keep research current** | Search can use SearXNG, Tavily, Exa, or the bundled keyless DDGS fallback, with date-aware queries and the same network controls as every other outbound request. |

---

## Quick start

### Install

**macOS, Linux, or WSL2**

```sh
curl -fsSL https://raw.githubusercontent.com/palindrome-rl/AGENT8088/main/install.sh | bash
```

**Windows (PowerShell)**

```powershell
iex (irm https://raw.githubusercontent.com/palindrome-rl/AGENT8088/main/install.ps1)
```

The installer provisions an isolated Python environment, installs the global `agent8088` command, and can run the setup wizard. No administrator access is required for the base install.

**What the installer provisions automatically:**

| Component | Linux / macOS | Windows |
| --- | --- | --- |
| Core agent (chat, tools, MCP, search) | yes | yes |
| Gateway adapters (Slack, Discord, WhatsApp, Telegram) | yes | yes |
| Playwright Chromium (`browse_page`) | yes | yes |
| Node.js 22 + WhatsApp bridge npm deps | yes | yes (portable, no admin) |
| Native sandbox runtime | yes (auto-setup) | hint only — needs an elevated terminal |

The `[dev]` extra (pytest, ruff, pip-audit) is **not** installed — run `uv pip install -e ".[dev]"` if you need the test suite.

### Configure and run

```sh
agent8088 --setup            # choose a provider, model, workspace, and search backend
agent8088                    # start the interactive agent
```

The setup wizard stores API keys in `~/.agent8088/.env` rather than `config.txt`. Start with a local Ollama model or select a hosted provider; the agent can switch models later with `/model` or `/models`.

> **Windows only:** the native sandbox runtime needs an elevated terminal to provision its restricted account + WFP egress filter. After install, open an elevated PowerShell and run `agent8088 --sandbox-setup`. On Linux and macOS the installer runs this automatically.

### A few useful commands

| Command | Purpose |
| --- | --- |
| `agent8088` | Start an interactive session. |
| `agent8088 --uninstall` | Remove the install dir, config, and env vars. |
| `agent8088 --gateway-setup` | Configure Slack, Discord, WhatsApp, Telegram, or email. |
| `agent8088 --gateway` | Run the messaging gateway. |
| `agent8088 --mcp-serve` | Expose Agent8088's safe tools over MCP stdio. |
| `/plan <task>` | Research, propose a plan, and wait for your approval before mutations. |
| `/capabilities` | Show the live tool, MCP, sandbox, skill, sub-agent, and guardrail configuration. |
| `/doctor` | Check local setup and report likely problems. |

---

## How Agent8088 stays in control

Agent8088 has three permission modes:

| Mode | Behaviour |
| --- | --- |
| **`readonly`** *(default)* | Read and inspect safely; request a one-time approval for writes, network access, scheduling, or non-safe shell commands. |
| **`full-auto`** | Work without per-action prompts inside the configured workspace. The always-on safety floor still applies. |
| **`plan-only`** | Research and present a plan first; approved work then uses the regular permission path. |

Some actions are blocked in every mode: credential paths, shell startup-file writes, destructive Git operations such as `push` and `reset --hard`, and system-prompt exfiltration. See the [security guide](docs/wiki/03-permissions-and-security.md) for the exact boundaries and configuration.

---

## CLI and messaging quick reference

The CLI and gateway share the same agent loop, session model, tool registry, and permission checks.

| Action | CLI | Messaging gateway |
| --- | --- | --- |
| Start a conversation | `agent8088` | Run `agent8088 --gateway`, then message an authorized account. |
| Start fresh or resume work | `/new`, `/sessions`, `/resume` | Per-chat and per-thread sessions persist automatically. |
| Change the model | `/model <provider:model>` or `/models` | `/model <provider:model>` |
| Inspect capabilities | `/capabilities` | `/capabilities` |
| Approve an action | Interactive terminal prompt | `/approve`, `/approve session`, or `/deny`; Discord also provides buttons. |
| Manage MCP servers | `/mcp`, `/mcp add`, `/mcp reload` | Available through the shared agent where appropriate. |

---

## Documentation

The versioned [documentation wiki](docs/wiki/README.md) is the source of truth for this branch.

| Read this | To learn about |
| --- | --- |
| [Getting started](docs/wiki/01-getting-started.md) | Installation, setup, sandboxing, and first run. |
| [Permissions and security](docs/wiki/03-permissions-and-security.md) | Permission modes, approvals, sensitive paths, network controls, and safety floors. |
| [Tools](docs/wiki/04-tools.md) | Every built-in tool, aliases, web-search backends, and tool-selection rules. |
| [Model providers](docs/wiki/05-model-providers.md) | Provider profiles, custom endpoints, keys, and fallback chains. |
| [Memory](docs/wiki/16-memory.md) | What gets remembered, how hybrid retrieval works, and the `/memory` command. |
| [MCP](docs/wiki/07-mcp.md) | Connecting MCP servers and serving Agent8088 tools to other agents. |
| [Messaging gateway](docs/wiki/08-messaging-gateway.md) | Slack, Discord, WhatsApp, Telegram, and email setup. |
| [Skills and sub-agents](docs/wiki/09-skills-and-subagents.md) | Bundled profiles, isolation, skills, and personas. |
| [CLI reference](docs/wiki/10-cli-reference.md) | Flags and slash commands. |
| [Architecture](docs/wiki/11-architecture.md) | The agent loop, front ends, permissions, and state on disk. |
| [Testing and verification](docs/wiki/12-testing-and-verification.md) | Local test, feature-verification, and release checks. |

---

## Contributing

Develop against `development` and run the suite in an isolated configuration:

```sh
git clone https://github.com/palindrome-rl/AGENT8088.git
cd Agent8088-Features-added
uv sync --all-extras
AGENT8088_CONFIG=/nonexistent uv run python -m pytest tests/ -q
```

See the [contribution guide](docs/wiki/14-contributing.md) for the required isolation rules, local verification commands, and PR conventions. For security reports, use [private vulnerability reporting](SECURITY.md); never include credentials or exploit details in a public issue.

## License

[MIT](LICENSE)

<div align="center">
  <img src="assets/palindrome-research-labs-footer.png" alt="Palindrome Research Labs" width="320">
</div>
