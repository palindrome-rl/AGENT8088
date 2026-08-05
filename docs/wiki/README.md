# Agent8088 Wiki

Complete reference for Agent8088 — a local-first AI agent with fine-tuned tool
calling, an enforced permission layer, OS-level sandboxing, MCP in both
directions, and messaging gateways.

Everything here was verified against the code in this repository rather than
copied from the README. Where the two disagree, the wiki notes it explicitly.

## Start here

| If you want to… | Read |
|---|---|
| Install it and run your first prompt | [Getting Started](01-getting-started.md) |
| Understand every config key | [Configuration](02-configuration.md) |
| Know what the agent is allowed to do | [Permissions & Security](03-permissions-and-security.md) |
| See every tool and what it takes | [Tools](04-tools.md) |
| Point it at a different model | [Model Providers](05-model-providers.md) |
| Understand command isolation | [Sandboxing](06-sandboxing.md) |
| Connect MCP servers, or expose Agent8088 as one | [MCP](07-mcp.md) |
| Run it in Slack / WhatsApp / Discord | [Messaging Gateway](08-messaging-gateway.md) |
| Use skills and sub-agents | [Skills & Sub-agents](09-skills-and-subagents.md) |
| Look up a flag or slash command | [CLI Reference](10-cli-reference.md) |
| Understand how the pieces fit | [Architecture](11-architecture.md) |
| Verify a change didn't break anything | [Testing & Verification](12-testing-and-verification.md) |
| Fix something that's broken | [Troubleshooting](13-troubleshooting.md) |
| Contribute code | [Contributing](14-contributing.md) |

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
| Sub-agent profiles | **4** — `coder`, `explore`, `general-purpose`, `researcher` |
| Bundled skills | **5** |
| Slash commands | **33** |
| Gateway platforms | **3** — Slack, WhatsApp, Discord |
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

## Known documentation drift

The top-level `README.md` is slightly ahead of / behind the code in two places.
The wiki uses the verified values:

| Claim in README | Actual |
|---|---|
| "13 model providers", listing Anthropic | **12** built-in; `anthropic` is *not* one of them. Anthropic works via a custom `api_mode=litellm` provider or through OpenRouter — see [Model Providers](05-model-providers.md) |
| Slash-command table lists 24 | **33** commands are registered — see [CLI Reference](10-cli-reference.md) |
| CLI flag list omits MCP-server flags | `--mcp-serve`, `--mcp-http`, `--mcp-port`, `--mcp-host` exist |
