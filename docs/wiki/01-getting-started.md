# Getting Started

[← Wiki index](README.md)

## Requirements

- **Python 3.10+**
- A model endpoint — either local [Ollama](https://ollama.com) or any
  OpenAI-compatible API key
- Optional: Node.js 20.11+ for the native sandbox and the WhatsApp bridge
- Optional: LibreOffice, for `.docx`/`.pptx`/`.xlsx`→PDF conversion,
  legacy `.doc`/`.ppt`/`.xls` reading, and Excel formula recalculation. It is
  ~350 MB and by far the slowest optional stage, so it is **opt-in**: the
  installers skip it unless you pass `--with-libreoffice` (bash) or
  `-WithLibreOffice` (PowerShell), or set `AGENT8088_INSTALL_LIBREOFFICE=1`.
  Everything else works without it. Add it at any time with:

  ```sh
  agent8088 --libreoffice-setup
  ```

  This uses WinGet on Windows, Homebrew on macOS, and apt/dnf/pacman on Linux.
  If the agent needs LibreOffice and it is missing, it tells you to run this
  command.

WinGet may prompt for elevation when installing LibreOffice, since it's a
per-machine install — that's WinGet's own prompt, not something Agent8088
requests. No admin rights are needed for the base install.

## Install

These commands install the public **AGENT8088-v1.2** branch.
`agent8088 --update` continues to follow that branch.

**macOS / Linux / WSL2**

```sh
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/palindrome-rl/AGENT8088/AGENT8088-v1.2/install.sh | AGENT8088_BRANCH=AGENT8088-v1.2 bash
```

**Windows (PowerShell)**

```powershell
$env:AGENT8088_BRANCH = 'AGENT8088-v1.2'
iex (irm https://raw.githubusercontent.com/palindrome-rl/AGENT8088/AGENT8088-v1.2/install.ps1)
```

The installer installs [uv](https://docs.astral.sh/uv/) if missing, clones the
repo into an isolated venv, exposes a global `agent8088` command, and writes a
default `config.txt` pointing at localhost Ollama.

**From a clone, to work on the code.** Clone the public release branch:

```sh
git clone --branch AGENT8088-v1.2 https://github.com/palindrome-rl/AGENT8088.git
cd AGENT8088
python -m venv .venv
.venv/bin/pip install -e ".[gateway,dev]"
```

Install the extras you actually need:

| Extra | Gives you |
|---|---|
| *(base)* | CLI, all 56 tools, MCP client and server, `browse_page`, keyless web search |
| `gateway` | Slack, WhatsApp, Discord, Telegram and Email adapters |
| `repository` | `gitingest`, for `repository_read`'s remote (github.com) mode |
| `repomap` | `tree-sitter` + `tree-sitter-language-pack`, for `repo_map` |
| `dev` | `pytest`, `ruff`, `pip-audit` for the test suite and lint/audit gates |
| `litellm`, `browser`, `search` | Aliases — all three are already base dependencies |

Playwright, `browser-use`, `litellm` and `ddgs` are **base** dependencies, not
extras — `browse_page` and the keyless search fallback should not depend on how
someone installed. The `browser`, `search` and `litellm` extras still exist as
aliases so older install commands keep working.

`browser-use` (the interactive-browsing engine behind `browse_page`) needs
Python 3.11 or newer, so it is skipped on a Python 3.10 install; everything else
still installs and `browse_page` says so if you call it.

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

The default mode is `full-auto`, so it simply writes the file. To see the
permission layer, restart with `agent8088 --mode readonly` (or run
`/mode readonly`) and ask again. This time it stops and asks:

```
Allow? (o=once / s=session / d=deny):
```

That prompt is the permission layer, not the model being polite. See
[Permissions & Security](03-permissions-and-security.md).

## Install the sandbox (recommended)

```sh
agent8088 --sandbox-setup
```

This installs the open-source Anthropic sandbox runtime so shell commands run
isolated from your filesystem and network. Without it Agent8088 falls back to
Docker, and if neither exists shell and code execution are refused outright
rather than prompted for. See [Sandboxing](06-sandboxing.md).

## Where things live

| Path | What |
|---|---|
| `~/.agent8088/config.txt` | Settings — flat `key=value` (mode `0600`) |
| `~/.agent8088/.env` | API keys and gateway tokens (mode `0600`) |
| `~/.agent8088/mcp.json` | User-level MCP servers |
| `.agent8088/mcp.json` | Project-level MCP servers (override user-level) |
| `~/.agent8088/gateway-sessions/` | Per-chat gateway history |
| `USER.md` | Optional persona / "about me" injected into the prompt (`user_file` config key, default: the installed app directory) |

On Windows, `config.txt` and `.env` live at `%LOCALAPPDATA%\agent8088\`.

## Next

- [Configuration](02-configuration.md) — every key explained
- [CLI Reference](10-cli-reference.md) — all flags and 45 slash commands
- [Tools](04-tools.md) — what the agent can actually do
