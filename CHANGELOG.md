# Changelog

All notable changes to Agent8088 are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v1.3 branch maintenance - 2026-10-16

### Improved

- When a run is stopped by its closing cap, the page or script is smoke-checked
  once, and the answer starts with a NOT VERIFIED line when the run saw a clear
  sign of trouble.
- A run that repeats itself is warned in steps before it is ended.
- Productive research may run more searches; a refused search ends with guidance
  and the files gathered so far.
- A relative `artifacts/` path is kept as written after a command changes
  directory.
- Credential scanning no longer flags plain words, MIME type lists, encoded URLs
  or source-code tokens.

Public installation commands, update channel, version 1.3.0, MIT licensing and
existing public-specific diagnostics and Web UI fixes remain preserved.

## [1.3.0] - 2026-10-15

The public channel moves to the `AGENT8088-v1.3` branch. Installers,
`agent8088 --update`, the Docker image and the wiki follow it. Existing
configuration and memory are preserved.

### Added

- `copy_file` and `move_file` copy an existing local file byte-for-byte (PDFs,
  images), for example to file an attachment into `Drive/Finance/`. A folder
  destination keeps the file's name. A missing folder is created only when you
  named it; otherwise the file stays in `artifacts/` and the reply says so.
  `move_file` only moves files from inside the project.
- Credential-shaped strings in pasted input and written files are detected, and
  the web chat warns before sending one. Product and version tokens are no
  longer reported as credentials.
- The model is told when its time or rounds are running out.

### Improved

- Completion checks compare each part of a request with what the run delivered,
  ask once for a lookup when a build needs facts the run never checked, and send
  a reply back while the latest automated check is still failing. Only a command
  that runs a check can mark a run as failing one.
- Page smoke tests keep clicking after the first control and judge the page as a
  whole. Script smoke tests tell "could not start here" from a crash, keep a
  failed start's output, and recognise failures that have since been fixed.
- A run that brings nothing new is asked to decide; a finished run is told to
  finish. Empty replies and unknown-tool calls count toward the in-a-row limits.
- Your rules are kept word for word through context compaction.
- A write diverted to `artifacts/` is checked where it landed, and is copied to
  the path you named. A supplied input file is no longer demanded as output.
- Native Windows shell commands start in the workspace.
- The plan file, web trace export, headless resume and sub-agent routing are
  more reliable.

### Security

- `copy_file` and `move_file` refuse another session's web attachment, as
  `read_text` already did.

### Fixed

- A plan file that cannot be written is logged instead of raising an error.

## v1.2 branch maintenance - 2026-10-14

### Improved

- Reasoning-only cutoffs temporarily disable thinking for a configurable number
  of following calls. Truncated tool arguments receive smaller-chunk guidance.
- Safe partial text writes can be recovered through normal permission and backup
  guards, with unfinished files tracked by full path until continued.
- Configured credentials are blocked in file content, append boundaries, final
  edited text and downloaded UTF-8 content; denial messages do not reveal values.
- Smoke checks skip scratch helpers, use the correct native Windows directory,
  and report browser-only JavaScript startup as unverified rather than passed.
- Verification uses recorded evidence and confirms failed audits before rollback;
  inconclusive results preserve changes and clearly report what remains unchecked.
- Browser smoke checks stop only their own browser processes and distinguish
  blocked external dependencies from real application errors.
- The local smoke-test server reliably rejects write requests on Windows,
  draining only a size- and time-limited request body before returning HTTP 405.
- Idle stream recovery works with OpenAI and LiteLLM; empty and reasoning-only
  cutoffs receive bounded recovery, with unfinished work reported at run limits.
- Conversation compaction preserves earlier summaries and carries forward verified
  checks and changed-file records instead of repeatedly rewriting the same facts.
- Existing files receive recoverable, size-limited backups before their first
  overwrite in a session, with configurable retention and storage limits.
- Nullable tool arguments and MCP schemas are validated consistently; semantic
  tool retrieval and native tool instructions remain configurable.

Public installation commands, update channel, version 1.2.0, MIT licensing and
existing public-specific diagnostics and Web UI fixes remain preserved.

## v1.2 branch maintenance - 2026-10-13

### Improved

- Shared completion checks catch unfinished work, missing outputs and unchecked
  changes with a bounded follow-up budget. `check_app` smoke-tests generated
  pages and scripts, with configurable reporting or enforcement.
- Adaptive output limits, thinking-off recovery and chunked file writes keep
  long replies and repetitive rewrites bounded. Successful tests no longer
  trigger redundant audits.
- Provider errors inside streams are retried; keyword tool ranking still works
  without embeddings, and MCP text results preserve structured data when present.
- Durable tasks can be deleted safely after their workers stop. Chat switching,
  queued commands and automatically named sessions retain the correct identity.
- Document extraction caching, persistent memory recall, Windows ESC handling
  and CLI reasoning scrollback are more reliable.
- Docker allowlist networking uses an egress proxy. Windows native sandbox
  documentation accurately describes write-only confinement and non-admin setup.
- The public Doctor page allows a bounded two minutes for live health probes;
  desktop and mobile acceptance checks wait for completed diagnostic results.

Public installation URLs, update channel, version 1.2.0, MIT licensing and
public-specific diagnostics remain preserved.

## v1.2 branch maintenance - 2026-10-12

### Improved

- Interactive CLI conversations automatically get a saved name after the
  first message, with source/model metadata and restart/resume support.
- The Web UI can start or resume another chat while a task runs. Transcripts,
  answers and approvals stay with their owning chat; queued messages retain
  their destination. Running chats are protected from rename, delete and reset.
- Session writes share the atomic, private file writer. Plan completion and
  queued-turn streaming state retain the correct session identity.
- Windows sandbox setup and execution consistently choose a supported Node.js
  runtime, preferring the managed installation. Setup can download checksummed
  Node.js 22.11.0 when needed without replacing an existing installation with an
  incomplete download.

Public installation commands, update channel, version 1.2.0, MIT license and
existing public-specific diagnostics and UI fixes are preserved.

## v1.2 branch maintenance - 2026-10-09

### Fixed

- Requests now start with 50 rounds consistently across the CLI, Web UI,
  gateways and direct engine calls; productive runs retain bounded growth.
- Resuming older sessions without a stored turn limit preserves the current
  setting instead of silently resetting it to 10.
- Time, token and cost budget messages identify the limit actually reached.
- Controller-generated output-location notes identify sandbox workspace output
  when it differs from the project folder.
- The Web UI `+` menu starts search and memory requests rather than backend
  configuration commands. Commands with ready attachments are refused without
  discarding the composer or files, including while another turn is running.

Public repository URLs, installation scripts, update channel, version 1.2.0,
MIT license and existing public-specific UI fixes are unchanged.

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

[1.3.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.3.0
[1.2.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.2.0
[1.0.0]: https://github.com/palindrome-rl/AGENT8088/releases/tag/v1.0.0
