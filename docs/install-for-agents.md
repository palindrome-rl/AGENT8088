# Installing Agent8088 — guide for AI agents

This file is written for an AI agent that a person has asked to install
Agent8088: Claude Code, OpenCode, Codex, Hermes Agent, Cursor, or any agent that
can run shell commands. Follow it step
by step. If you are a person, the [Quick start](../README.md#quick-start) is
shorter.

## Rules

- Run the commands below exactly. Do not rewrite the installer, add `sudo`, or
  pipe it through another tool.
- Ask the person before anything that needs their choice or their secrets. Never
  invent, guess or print an API key, and never ask them to paste a key into this
  chat: they type keys into Agent8088's own setup (step 5).
- Do not disable antivirus, firewalls or execution policies beyond the
  per-command `-ExecutionPolicy Bypass` shown here.
- The install takes 5–20 minutes. Run it in the background or with a long
  timeout, and read the install log instead of re-running it.

## 1. Ask the person

Ask these before installing, and use the answers below:

1. **Branch** — `AGENT8088-v1.3` (this public release channel) unless they name another.
2. **Mem0 memory engine** (optional, larger download) — yes or no. Default no.
3. **LibreOffice** for Word/Excel/PowerPoint conversion (optional, large) — yes
   or no. Default no.
4. **Which model provider** they will use: a local Ollama model (no key) or a
   hosted provider such as OpenRouter, Ollama Cloud, OpenAI or Anthropic (needs
   their key in step 5).

## 2. Check the machine

| Check | Command | Needed |
|---|---|---|
| OS | `uname -s` (macOS/Linux/WSL) or `$PSVersionTable.OS` (Windows) | picks the installer |
| git | `git --version` | yes |
| Repository access | `git ls-remote https://github.com/palindrome-rl/AGENT8088.git HEAD` | yes: this repository is public; no GitHub login or token is needed. If access fails, report the network/proxy error and stop. |
| Disk | about 4 GB free | yes |

Python, Node.js, Playwright Chromium and uv are installed by the installer; do
not install them yourself.

## 3. Run the installer

Replace `AGENT8088-v1.3` with the chosen public branch in both the installer fetch and
`AGENT8088_BRANCH`, and set the two yes/no answers
(`1` = yes, `0` = no).

**macOS, Linux or WSL2** (bash):

```sh
set -o pipefail
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/palindrome-rl/AGENT8088/AGENT8088-v1.3/install.sh \
  | AGENT8088_BRANCH=AGENT8088-v1.3 AGENT8088_INSTALL_MEM0=0 AGENT8088_INSTALL_LIBREOFFICE=0 \
    bash -s -- --skip-setup
```

**Windows** (PowerShell, not `cmd.exe`):

```powershell
$installer = Join-Path $env:TEMP ("agent8088-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
Invoke-RestMethod https://raw.githubusercontent.com/palindrome-rl/AGENT8088/AGENT8088-v1.3/install.ps1 -OutFile $installer -ErrorAction Stop
$env:AGENT8088_BRANCH = "AGENT8088-v1.3"
$env:AGENT8088_INSTALL_MEM0 = "0"
$env:AGENT8088_INSTALL_LIBREOFFICE = "0"
powershell -NoProfile -ExecutionPolicy Bypass -File $installer -SkipSetup -SkipTerminalCheck
```

`--skip-setup` / `-SkipSetup` skips the interactive setup wizard, which needs a
person at the keyboard (step 5). `-SkipTerminalCheck` stops the Windows
installer from relaunching itself in a new Windows Terminal window, which an
agent cannot see.

These fetch the installer from the selected public release branch. Neither
GitHub CLI nor a GitHub account or token is required.

**Progress and errors** are written to the install log:
`~/.agent8088/install.log` (macOS/Linux) or
`%LOCALAPPDATA%\agent8088\install.log` (Windows). If the installer fails, read
the end of the log: it names the cause and the command to fix it. Report that to
the person rather than retrying blindly.

## 4. Verify

Open a new shell (so `PATH` includes the new command), then run:

```sh
agent8088 --version
agent8088 --doctor
```

The install succeeded when `--version` prints a version and `--doctor` shows no
failed checks other than the model provider, which is configured next. Warnings
such as "sandbox network not enforced" (Windows) or "search using ddgs" are
expected and do not block use.

If `agent8088` is not found, use the full path:
`~/.agent8088/agent8088/venv/bin/agent8088` (macOS/Linux) or
`%LOCALAPPDATA%\agent8088\agent8088\venv\Scripts\agent8088.exe` (Windows).

## 5. Hand over to the person

Tell the person the install is done and ask them to run, in their own terminal:

```sh
agent8088 --setup
```

The wizard picks the provider and model, stores the API key in
`~/.agent8088/.env` on macOS/Linux or `%LOCALAPPDATA%\agent8088\.env` on Windows
(or your custom `AGENT8088_HOME`), and chooses the web search backend. With a local Ollama
model no key is needed.

Then they can start the agent with `agent8088` (terminal) or `agent8088 --web`
(browser UI).

## 6. Report

Finish with a short summary for the person: the branch installed, the version
from `agent8088 --version`, anything `--doctor` flagged, and the one command
left for them (`agent8088 --setup`).
