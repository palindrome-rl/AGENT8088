# Agent8088 Wiki

Complete reference for Agent8088 — a local-first AI agent with fine-tuned tool
calling, an enforced permission layer, OS-level sandboxing, MCP in both
directions, and messaging gateways.

Everything here was verified against the code in this repository rather than
copied from the README. Where the two disagree, the wiki notes it explicitly.

> **This directory is the source of truth.** It is versioned with the code and
> reviewed in PRs. The [GitHub Wiki tab](https://github.com/palindrome-rl/AGENT8088/wiki)
> is a generated mirror — run `python scripts/sync_wiki.py` after changing a
> page here to republish it. Edits made in the wiki UI are overwritten by the
> next sync.

## Start here

New to Agent8088? [Getting Started](01-getting-started.md) takes you from
install to your first prompt. In a hurry, [the FAQ](15-faq.md) answers most
first-day questions.

## Guides

Read these to understand how a subsystem behaves.

| Guide | Covers |
|---|---|
| [Permissions & Security](03-permissions-and-security.md) | What the agent may do, approvals, the always-on floor, network controls |
| [Sandboxing](06-sandboxing.md) | How commands are isolated, and why there is no unsandboxed fallback |
| [Model Providers](05-model-providers.md) | Provider profiles, custom endpoints, keys, fallback chains |
| [MCP](07-mcp.md) | Connecting MCP servers, and exposing Agent8088 as one |
| [Messaging Gateway](08-messaging-gateway.md) | Slack, WhatsApp, Discord, Telegram and Email |
| [Skills & Sub-agents](09-skills-and-subagents.md) | Bundled profiles, isolation, skills, personas |
| [Docker](17-docker.md) | Run in a container without a system-wide install |

## Reference

Look things up here.

| Reference | Contains |
|---|---|
| [CLI Reference](10-cli-reference.md) | Every flag, slash command, keybinding and exit code |
| [Configuration](02-configuration.md) | Every config key, the `.env` store, key resolution order |
| [Tools](04-tools.md) | All 21 built-in tools, their modes and arguments |
| [Memory](16-memory.md) | Persistent memory: what it stores, how retrieval works, `/memory` |
| [FAQ](15-faq.md) | Short answers to the common questions |
| [Troubleshooting](13-troubleshooting.md) | Symptom-first fixes |

## Development

| Page | For |
|---|---|
| [Architecture](11-architecture.md) | How the agent loop, front ends and permission layer fit together |
| [Testing & Verification](12-testing-and-verification.md) | The local gates to run before opening a PR |
| [Contributing](14-contributing.md) | Isolation rules, PR conventions, publishing the wiki |

## What Agent8088 is

A single-process agent that runs on your machine. It talks to any
OpenAI-compatible endpoint (local Ollama or a hosted API), calls tools to get
real work done, and gates every side effect behind a permission layer that
defaults to read-only.

At a glance, verified against the current tree:

| | |
|---|---|
| Built-in tools | **21** |
| Built-in model providers | **12** (plus custom OpenAI-compatible and litellm) |
| Permission modes | **3** — `readonly`, `full-auto`, `plan-only` |
| Sub-agent profiles | **5** — `auditor`, `coder`, `explore`, `general-purpose`, `researcher` |
| Bundled skills | **5** installed, plus **20** behaviour skills in `skills/*.yaml` |
| Slash commands | **37** |
| Gateway platforms | **5** — Slack, WhatsApp, Discord, Telegram, Email |
| Python | **3.10+** |

## The design idea worth knowing up front

Agent8088 assumes the model will sometimes be wrong, and sometimes be
manipulated by content it reads. So the safety properties do not live in the
prompt — they live in code that runs regardless of what the model decides:

- **Read-only by default.** Writes, shell, network, cron and browser actions
  are refused unless you approve them for that one action.
- **An always-on floor.** Some things are refused in *every* mode, even
  full-auto, even after you approve an escalation: reading or writing
  credential files, writing shell startup files, `git push`/`reset --hard`,
  and requests for the agent's own system prompt.
- **External content is fenced.** Anything fetched from the web or an MCP
  server is wrapped in `<<<EXTERNAL_UNTRUSTED_CONTENT>>>` markers with
  chat-template tokens stripped, so a page cannot forge a system turn.

[Permissions & Security](03-permissions-and-security.md) covers all of it.

## Re-deriving the numbers

The counts above drift whenever someone adds a tool, a command or an adapter, so
derive them from the tree rather than trusting this page:

```sh
export AGENT8088_CONFIG=/nonexistent AGENT8088_HOME="$(mktemp -d)"   # never import bare

grep -c '^[a-z]' src/agent8088/tools.txt                             # 21 tools
uv run python -c "import agent8088.cli as c; print(len(c.COMMANDS))" # 37 commands
ls src/agent8088/gateway/platforms/*.py | grep -vc 'base\|__init__'  # 5 platforms
ls src/agent8088/agents/ | wc -l                                     # 5 sub-agents
uv run python -c "import agent8088.providers as p; print(len(p.BUILTIN_PROVIDERS))"  # 12
```

The snippet is POSIX (`export`, `grep -c`, `ls`, `wc -l`); on Windows run it under
WSL or Git Bash, or use the PowerShell equivalents (`$env:`, `Select-String`,
`Get-ChildItem | Measure-Object`).

The `AGENT8088_CONFIG` / `AGENT8088_HOME` line is not decoration: importing
`agent8088.cli` bare reads — and can migrate — your real `~/.agent8088/config.txt`.
See [Testing & Verification](12-testing-and-verification.md#isolation-rules-for-anything-you-write).

One count that is deliberately *not* derivable: there is no `anthropic` built-in
provider. Claude is reached through OpenRouter or a custom `api_mode=litellm`
profile — see [Model Providers](05-model-providers.md#reaching-anthropic--claude).
