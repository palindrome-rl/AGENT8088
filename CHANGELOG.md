# Changelog

All notable changes to Agent8088 are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v1.2 branch maintenance - 2026-10-07

### Improved

- Shared capability health reporting across the CLI, Web UI, gateway and Doctor.
- Search fallback/recovery notices, transient DDGS retries and bounded dynamic
  search allowances while queries continue finding new evidence.
- Working-directory recovery and failure diagnostics, output-limit recovery,
  verification accounting and bounded post-check loops.
- CLI readability, toggle status commands, timestamped diagnostic exports,
  memory capture, provider discovery and local-model context reporting.
- Scanned-document OCR, gateway dependency reporting and Docker browser access.

### Fixed

- Windows OpenCodeReview version pin scope under `iex`, missing download
  diagnostics, and false readiness based only on an executable's presence.
- OpenCodeReview now retries transient network failures with bounded backoff;
  both installers verify the pinned executable before reporting success.

Public version metadata remains 1.2.0. Public installation URLs, MIT licensing,
CI, support files and existing installation-recovery safeguards are preserved.

## [1.2.0] - 2026-09-29

### Added

- Small-model-first execution: efficient prompts, minimized tool schemas and
  model-aware routing. `/local check` probes your hardware and
  `/local available` lists local Ollama models that fit it.
- Verification-gated completion: tasks are checked against their required
  outputs, tests and observable tool results before completion is reported.
- Robust context management: pre-call budgeting, overflow prevention,
  compaction, content handles and persistent task state.
- A web UI alongside the CLI, with live diffs.
- Local OCR for reading screenshots and scanned PDFs without a vision model.
- One-command installers for macOS, Linux, WSL2 and Windows.
- A Docker image, published to `ghcr.io/palindrome-rl/agent8088`.
- MCP client and server, skills, focused sub-agents, and Slack, Discord,
  WhatsApp, Telegram and email gateways that share one agent loop and
  permission layer.
- An optional audit log (`audit_log=1`).

### Security

- `readonly`, `plan-only` and `full-auto` permission modes, a native OS sandbox
  for shell commands, credential protection, and SSRF and egress controls.

## [1.0.0] - 2026-08-18

### Added

- A local-first, tool-using agent runtime with one session and permission layer
  shared by the CLI and the messaging front ends.
- A three-tier permission engine (`readonly`, `plan-only`, `full-auto`) with
  per-action approval, credential-path protection, and SSRF and egress controls
  at the tool-call boundary.
- Built-in tools for the filesystem, shell, Git, Chromium browsing, web search
  (SearXNG, Tavily, Exa, DDGS) and scheduling, also served over MCP stdio
  (`--mcp-serve`), plus connections to external MCP servers.
- A native OS sandbox for shell commands, with a Docker fallback.
- Restricted sub-agent profiles for context-isolated work.
- Hybrid keyword and semantic memory over a local SQLite store.
- 12 provider profiles, custom OpenAI-compatible endpoints and local Ollama,
  with fallback chains on retryable failures.

[1.2.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.2.0
[1.0.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.0.0
