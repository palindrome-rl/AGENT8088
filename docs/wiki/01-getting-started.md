# Getting Started

[← Wiki index](README.md)

## Requirements

- **Python 3.10+**
- A model endpoint — either local [Ollama](https://ollama.com) or any
  OpenAI-compatible API key
- Optional: Node.js 20.11+ for the native sandbox and the WhatsApp bridge

No admin rights are needed for the base install.

## Install

**macOS / Linux / WSL2**

```sh
curl -fsSL https://raw.githubusercontent.com/palindrome-rl/AGENT8088/main/install.sh | bash
```

**Windows (PowerShell)**

```powershell
iex (irm https://raw.githubusercontent.com/palindrome-rl/AGENT8088/main/install.ps1)
```

The installer installs [uv](https://docs.astral.sh/uv/) if missing, clones the
repo into an isolated venv, exposes a global `agent8088` command, and writes a
default `config.txt` pointing at localhost Ollama.

**From a clone, for development:**

```sh
git clone https://github.com/palindrome-rl/AGENT8088.git
cd Agent8088-Features-added
python -m venv .venv
.venv/bin/pip install -e ".[gateway,dev]"
```

Install the extras you actually need:

| Extra | Gives you |
|---|---|
| *(base)* | CLI, all 21 tools, MCP client and server, `browse_page`, keyless web search |
| `gateway` | Slack, WhatsApp, Discord, Telegram and Email adapters |
| `dev` | `pytest` for the test suite |
| `litellm` | Only for a provider profile with `api_mode=litellm` |

Playwright and `ddgs` are **base** dependencies, not extras — `browse_page` and
the keyless search fallback should not depend on how someone installed. The
`browser` and `search` extras still exist as aliases so older install commands
keep working.

> Without the `gateway` extra the Slack/Discord tests fail at import rather
> than skipping — see [Troubleshooting](13-troubleshooting.md).

Playwright is included in the base install. Install its Chromium browser once
to enable `browse_page`:

```sh
playwright install chromium
```

## Verify

```sh
agent8088 --version
```

## Configure a model

```sh
agent8088 --setup
```

The wizard asks for:

1. **Working directory** — where the agent may read and write (default `~`)
2. **Provider** — a fuzzy picker over the 12 built-ins, plus *Custom
   OpenAI-compatible*
3. **API key** — hidden input, stored in `~/.agent8088/.env` (mode `0600`),
   never in `config.txt`
4. **Model** — fetched live from the provider's `/v1/models` where supported,
   otherwise typed
5. **Web search** — pick a backend. SearXNG is offered first when Docker is
   available (the wizard provisions it on `127.0.0.1`); otherwise the bundled
   keyless `ddgs` fallback is already active and needs nothing. Tavily and Exa
   are optional API-key backends. Re-running setup offers **Keep current
   setting**. No endpoint is configured for you if you skip this — see
   [Pointing web search at a SearXNG](04-tools.md#pointing-web-search-at-a-searxng)
   to set one later.

Re-running the wizard pre-fills what you already have, so pressing Enter keeps
the existing value instead of clearing it.

To change only the model later:

```sh
agent8088 --model-setup
```

## First run

```sh
agent8088
```

You get a banner with the active model, tool count and permission mode, then a
prompt. Try:

```
> what files are in this directory?
```

That runs `execute_shell` with `ls` — a read-only command, so it needs no
approval. Now try something that mutates:

```
> create a file called hello.txt with the text hi
```

The agent is in **readonly** mode, so instead of writing it asks:

```
Allow write to /path/hello.txt? [y/N]
```

That prompt is the permission layer, not the model being polite. See
[Permissions & Security](03-permissions-and-security.md).

## Install the sandbox (recommended)

```sh
agent8088 --sandbox-setup
```

This installs the open-source Anthropic sandbox runtime so shell commands run
isolated from your filesystem and network. Without it Agent8088 falls back to
Docker, and if neither exists it asks before every local command. See
[Sandboxing](06-sandboxing.md).

## Where things live

| Path | What |
|---|---|
| `~/.agent8088/config.txt` | Settings — flat `key=value` (mode `0600`) |
| `~/.agent8088/.env` | API keys and gateway tokens (mode `0600`) |
| `~/.agent8088/mcp.json` | User-level MCP servers |
| `.agent8088/mcp.json` | Project-level MCP servers (override user-level) |
| `~/.agent8088/gateway-sessions/` | Per-chat gateway history |
| `USER.md` | Optional persona / "about me" injected into the prompt |

On Windows, `config.txt` lives at `%LOCALAPPDATA%\agent8088\config.txt`.

## Next

- [Configuration](02-configuration.md) — every key explained
- [CLI Reference](10-cli-reference.md) — all flags and 37 slash commands
- [Tools](04-tools.md) — what the agent can actually do
