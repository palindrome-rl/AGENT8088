# Configuration

[← Wiki index](README.md)

`config.txt` is a flat `key=value` file with `#` comments — no YAML, no nesting.
Written with mode `0600`.

**Location**, in resolution order:

1. `$AGENT8088_CONFIG` env var if set
2. `./config.txt` in the current directory — **always preferred if running inside an active Python venv** (detected via `VIRTUAL_ENV` env var or `sys.prefix` differing from `sys.base_prefix`), even before the file exists; otherwise only if it already exists
3. `~/.agent8088/config.txt`
4. `%LOCALAPPDATA%/agent8088/config.txt` (Windows)
5. the packaged `src/agent8088/config.txt` as a last-resort default

Running inside an activated project venv means `config.txt` lives in that project directory (created by `--setup`) instead of the global install, so each venv-based project can own its own configuration.

Secrets do **not** belong here — see [API keys](#api-keys-and-the-env-store).

## Paths and workspace

| Key | Default | Purpose |
|---|---|---|
| `allowed_paths` | `.` | Roots the agent may touch at all; `.` is the launch workspace. Anything outside is refused before any other check. |
| `project_root` | cwd | Base for relative paths. A folder that does not exist here is ignored with a warning, and the workspace is chosen as if the key were unset. |
| `shell_cwd` | cwd | Working directory for shell commands. Checked before every command: if it does not exist or cannot be entered, commands start in the launch folder, or else `project_root`, but only inside `allowed_paths` and outside `blocked_paths`. The model is told once, the switch is logged and traced (`cwd_repaired`), and `/doctor` shows it. With no allowed fallback, commands fail with `working_directory_missing`. |
| `no_prompt_paths` | (empty) | Writes here are auto-approved, no prompt. |
| `prompt_paths` | `~` | Writes here require per-action approval. Code's own fallback if the key is absent entirely is `.`, but the shipped `config.txt` sets it to `~` explicitly, so that's the real default for anyone running the packaged config. |
| `blocked_paths` | (empty) | Writes here are **always** refused, even in full-auto. |
| `read_paths` | (empty) | If set, reads outside these escalate. |

The three write zones are checked in order: blocked → no-prompt → prompt. See
[Permissions & Security](03-permissions-and-security.md#write-path-zones).

## Model and providers

| Key | Purpose |
|---|---|
| `default_provider` | Which provider to use when none is specified. |
| `provider.<name>.base_url` | OpenAI-compatible endpoint. |
| `provider.<name>.model` | Model id. |
| `provider.<name>.api_key_env` | Name of the env var / `.env` entry holding the key. **Preferred.** |
| `provider.<name>.api_key` | Literal key. Legacy — migrated to `.env` on first run. |
| `provider.<name>.api_mode` | `openai` (default) or `litellm`. |
| `provider.<name>.native_tools` | Use the provider's native function-calling API. Defaults to `1` for every provider except `ollama`, which uses the prompt-based convention. |
| `tool_call_format` | `markers` by default; opt-in `native` omits text-marker lessons when native tool calling is active. |
| `tool_selection_min_similarity` | `0.35`: semantic similarity required to offer a tool when keyword ranking has no matches. |
| `provider.<name>.context_window` | Context window for this provider profile. Overrides the global value. |
| `provider.<name>.max_completion_tokens` | Maximum output tokens for this provider profile. Overrides the global value. |
| `provider.<name>.temperature` | Sampling temperature for one provider; overrides the session value, including `/temp`, on that provider's requests. Useful when a server expects a particular sampling setting. |
| `provider.<name>.reasoning_budget_tokens` | Thinking-token budget for that provider, overriding `reasoning_budget_tokens`; `0` turns it off for the provider. |
| `provider.<name>.reasoning_budget_extra_body` | JSON object that tells that provider the budget, with `{budget}` standing for the number, e.g. `{"thinking": {"budget_tokens": {budget}}}`. Without it only OpenRouter is configured (`{"reasoning": {"max_tokens": N}}`); other providers are left alone. |
| `provider.<name>.extra_body` | JSON object sent as additional request fields for that provider, such as vLLM `chat_template_kwargs`. Invalid JSON is ignored with a warning. Only configure fields your server accepts. |
| `provider.<name>.length_retry_extra_body` | JSON fields sent only on the retry after a length cut-off, to turn thinking off for that one call. Defaults by provider: Ollama `{"think": false}`, vLLM `{"chat_template_kwargs": {"enable_thinking": false}}`, others `{"reasoning_effort": "none"}`. A provider that rejects them gets the call again without them. |
| `fallback_models` | Comma-separated `provider:model` chain for transient errors, missing models, capacity failures, and independent-provider authentication failures. |
| `tool_selection` | Which tool schemas go on the wire for native function-calling. `hybrid` (default) sends the ~10 most relevant via keyword + embedding retrieval, falling back to `full` when the two signals disagree or embeddings are unavailable; `full` always sends every schema; `auto` enables hybrid only for the measured `provider:model` entries in `tool_selection_models`. `/tool-selection <mode>` changes it live. |
| `tool_selection_models` | Comma-separated `provider:model` entries `tool_selection=auto` treats as measured-safe for hybrid mode. |
| `context_window` | Token budget for history trimming. |
| `max_completion_tokens` | Global output-token ceiling when the active model has no reviewed limit or provider override. |
| `chars_per_token` | Characters per token for the context-size estimate (default `4`). Code, JSON and Qwen tokenizers run nearer `3`; set it lower if requests are rejected for exceeding the context length. |
| `model_base_url` | Endpoint used when no `provider.<name>` profile is active (legacy flat setting; the `OLLAMA_URL` environment variable also sets it). Default `http://localhost:11434/v1`. |
| `model_name` | Model id for `model_base_url` (the `MODEL_NAME` environment variable also sets it). |
| `gemma_base_url`, `gemma_model_name` | Endpoint and model for the legacy `USE_GEMMA4=1` switch. Defaults `http://localhost:8003/v1` and `gemma-4-12B-it-Q4_K_M.gguf`. |
| `timeout_seconds` | Per-request timeout (default `120`). |

Transient provider failures are retried before the turn fails or `fallback_models`
takes over:

| Key | Default | Purpose |
|---|---|---|
| `api_max_retries` | `5` | Retries per call on retryable errors (408/429/5xx, connection, errors the provider reports inside a stream). `0` disables. With the default delays the retries span about 30 s. |
| `api_retry_initial_delay_ms` | `1000` | First backoff delay. |
| `api_retry_max_delay_ms` | `30000` | Ceiling for the exponential backoff. |
| `api_retry_jitter_ratio` | `0.1` | Random jitter added to each delay (0-1), so parallel retries do not sync up. |
| `stream_idle_seconds` | `120` | Maximum silence after streaming starts before a retryable idle error. Works with OpenAI and LiteLLM streams; `0` disables it. SDK request timeouts still bound the initial response wait. |

### Fusion

| Key | Default | Meaning |
|---|---|---|
| `fusion_panel` | `""` | `provider:model,provider:model,...`; empty = auto-discover every provider with a working key. Overridden per-call by `/fusion --panel ...` |
| `fusion_judge_provider` | `""` | Provider to use as judge; empty = the currently active provider |
| `fusion_judge_model` | `""` | Model to use as judge; empty = the session's current model |
| `fusion_max_panel` | `6` | Maximum number of models in the panel |
| `fusion_member_timeout_s` | `60` | Per-panel-member timeout in seconds |
| `fusion_max_workers` | `8` | Thread pool size for parallel panel calls |
| `fusion_panel_max_tokens` | `1200` | Max completion tokens per panel member |
| `fusion_judge_max_tokens` | `500` | Max completion tokens for the judge |

Sampling: `frequency_penalty`, `presence_penalty`, and `/temp` for the session
temperature. A `provider.<name>.temperature` setting takes precedence for that
provider. Details in [Model Providers](05-model-providers.md).

## Security

| Key | Default | Purpose |
|---|---|---|
| `allowed_sensitive_files` | (empty) | Escape hatch — comma-separated exact paths to exempt; relative paths resolve from the workspace. |
| `deny_commands` | (empty) | Shell commands to refuse (fnmatch globs). Refused in every mode. |
| `allow_commands` | (empty) | If set, the **only** shell commands permitted (fnmatch globs). `deny_commands` still wins, and no allowlist re-enables the unrecoverable floor. |
| `readonly_safe_commands` | (built-in list) | Commands treated as safe inspection in readonly mode. |
| `ssrf_allow_hosts` | (empty) | Private/internal hosts the agent may reach, as `host` or `host:port`. A loopback `search_base_url` is allowed automatically for its exact host:port, so a local SearXNG needs no entry; anything else internal does. Add a LAN SearXNG here — `/search setup` does it for you. See [Pointing web search at a SearXNG](04-tools.md#pointing-web-search-at-a-searxng). |
| `web_search_provider` | `auto` | At startup, selects a verified SearXNG when available, otherwise a fallback. |
| `web_search_no_prompt` | `1` | Approval-free search, permitted **only** while the effective pin is a loopback or allowlisted-private SearXNG. Inert with no endpoint configured, so a fresh install still prompts on every `ddgs` search. |
| `search_base_url` | (unset) | SearXNG endpoint, ending at `search?q=`. No default — nothing is assumed about your network. `https://` is required for public hosts. See [Pointing web search at a SearXNG](04-tools.md#pointing-web-search-at-a-searxng). |
| `searxng_host_port` | `8888` | Loopback port for the container `/search setup` provisions. Change it if 8888 is taken. Always published to `127.0.0.1` only. |
| `search_date_augmentation` | `1` | Append the current year (or month, for "today"/"this week" questions) to a search query that means "as of now" and names no year of its own. Set `0` to send queries exactly as the model wrote them. |
| `web_search_results` | `5` | Results per search (max 20). |
| `web_search_max_per_turn` | `6` | Searches one request may *start* with before the model must answer from what it has. When it is reached and the latest two searches each returned mostly pages the request had not seen, it grows by `web_search_extension`; rephrasing one question returns the same pages and never earns more. A search that returns no pages (for example, a throttled backend) is skipped rather than counted against the request, but each extension still needs a search that found new pages since the last one. `0` removes the cap. |
| `web_search_extension` | `3` | Searches granted each time the allowance grows. |
| `web_search_ceiling_multiplier` | `6` | Hard search ceiling as a multiple of `web_search_max_per_turn` (default 36). Up to three times the starting cap two productive searches in a row earn more; past that it takes three in a row, so only a request whose searches keep finding new pages gets that far. `1` disables growth and restores a fixed cap. When the allowance is spent the model is told what to do instead (fetch one page with the shell or a read tool, or go on to the work), `web_search` is no longer offered, and if nothing has been written for a build request it gets one extra message to start writing before the run can end. |
| `ssrf_allow_private` | `0` | `1` opens the entire private network. Prefer the allowlist. |
| `allowed_domains` | (empty) | If set, the **only** public hosts the agent may reach. Empty means all are reachable. |
| `blocked_domains` | (empty) | Public hosts the agent may never reach. Wins over `allowed_domains`. |
| `max_command_chars` | `16384` | Commands longer than this are refused rather than analysed. |
| `audit_log` | `0` | `1` appends one redacted JSON line per gated tool decision. Turn this on for any gateway deployment. |
| `audit_log_path` | `<data dir>/audit.jsonl` | Where the audit trail is written (mode 0600). |
| `block_secrets_in_writes` | `1` | Refuse to write a file whose content contains a credential from your configuration (an exact match of a configured key, token, secret or password of 12 or more characters), the same rule outbound requests follow. Append boundaries, final edited text, and downloaded UTF-8 content are checked too. The reply names no value. `0` allows it. |
| `audit_max_detail` | `512` | Truncation length for the audit `detail` field. |
| `model_telemetry` | `0` | `1` records local metadata-only model-call health events. |
| `model_telemetry_path` | `<data dir>/model-telemetry.jsonl` | Local telemetry path (mode 0600). |

Domain matching is dot-anchored, so `allowed_domains=example.com` permits
`docs.example.com` but **not** `evilexample.com`.

Both domain lists are checked *before* the SSRF DNS lookup: a host the policy
already rejects is never resolved, so the attempt does not reach that domain's
nameserver.

## Approvals

Flat keys rather than a nested block, so every approval setting is greppable.

| Key | Default | Purpose |
|---|---|---|
| `denial_breaker_threshold` | `3` | Consecutive denials before the request stops and reports instead of retrying. `0` disables. |
| `cron_mode` | `deny` | What an **unattended** run does at an approval gate. `deny` refuses and tells the model to report it; `approve` treats the gate as granted. Neither touches the always-on floor. |
| `destructive_slash_confirm` | `1` | `/reset` asks before discarding a conversation. |
| `mcp_reload_confirm` | `1` | `/mcp reload` asks before dropping the tool cache. |

| `default_permission_mode` | `full-auto` | Startup permission mode when neither `--mode` nor `AGENT8088_PERMISSION` is set. **This key does not appear in the shipped `config.txt` at all** — it exists only as a code-level fallback in `engine.py` (`APP_CONFIG.get("default_permission_mode", "full-auto")`), verified by importing the engine with a nonexistent config and reading `PERMISSION_MODE`, which comes back `full-auto`. An earlier version of this page (and of [Permissions & Security](03-permissions-and-security.md)) stated the default is `readonly`; that is wrong for an out-of-the-box install — set `default_permission_mode=readonly` in `config.txt`, or launch with `--mode readonly`, to get that behavior. |

There is deliberately no separate "approval mode" setting.
[`--mode` / `permission_mode`](03-permissions-and-security.md#the-three-permission-modes)
already decides what is gated; a second axis that could also wave a gate through
would mean `readonly` plus one other key silently behaves like `full-auto`.

Scheduled runs created by `schedule_task` set `AGENT8088_UNATTENDED=1` themselves,
so `cron_mode` applies without extra setup. The variable is read once at startup,
not per call.

## Sandbox

| Key | Default | Purpose |
|---|---|---|
| `sandbox_backend` | `auto` | `auto` → native, then Docker. `native` or `docker` can force one backend; there is no unsandboxed fallback. |
| `sandbox_runtime_version` | pinned | Version of the native runtime to install. |
| `sandbox_allowed_domains` | (empty) | Domains reachable from inside the sandbox; `default` adds common package registries and code hosts. |
| `sandbox_network` | (empty) | Explicitly setting `open` removes network filtering for Docker commands. In `auto` mode this prefers Docker on Linux/macOS; an explicit `native` backend remains native. Linux uses host networking; macOS uses bridge networking, where host services need `host.docker.internal`, not `localhost`. This can expose reachable services and allow project data to leave the sandbox; enable only for trusted work. Filesystem protections still apply. |
| `docker_autostart` | `1` | Start an already-installed Docker Desktop on Windows/macOS when needed for sandbox fallback or managed SearXNG recovery. Set `0` to disable; `AGENT8088_DOCKER_AUTOSTART=0` also disables it. Does not install Docker or start a Linux system daemon. |
| `docker_image` / `docker_network` | `python:3.11-slim` / `none` | Docker fallback settings; `docker_network=allowlist` applies `sandbox_allowed_domains` through a proxy container. |
| `docker_pull_seconds` | `300` | Time allowed for pulling a missing container image. Separate from a tool's own timeout, so a first-run pull does not fail as an unexplained timeout. |

## Gateway

| Key | Default | Purpose |
|---|---|---|
| `slack_enabled` / `whatsapp_enabled` / `discord_enabled` / `email_enabled` / `telegram_enabled` | `0` | Enable a channel. Any combination is allowed; the wizard enables one at a time. |
| `slack_allowed_users` etc. | (empty) | Comma-separated user ids permitted per platform. **Empty means nobody** — fail-closed. |
| `strict_platform_allowlist` | `1` | Refuses an id listed under a *different* platform's line. Set `0` only as a temporary migration aid. |
| `gateway_permission_mode` | `readonly` | `readonly` routes writes to chat approval; `edit` disables prompts. |
| `gateway_rate_limit_per_min` | `20` | Per-user messages per minute, slash commands included. `0` disables. |
| `whatsapp_mode` | `self-chat` | `self-chat` or `bot`. |
| `whatsapp_session_dir` | | Baileys session directory. |
| `whatsapp_bridge_port` | `3000` | Local bridge port. |

See [Messaging Gateway](08-messaging-gateway.md).

## MCP

| Key | Default | Purpose |
|---|---|---|
| `mcp_startup_wait_seconds` | `20` | How long a turn waits for MCP servers to connect at startup before going ahead without their tools. Late servers are picked up on a later turn. |
| `mcp_server_allow_writes` | `0` | `1` exposes `write_file` over `--mcp-serve`. Writes are **unattended** — MCP has no approval channel. |

MCP *servers you connect to* are configured in `mcp.json`, not here. See
[MCP](07-mcp.md).

## Memory

On by default. Full explanation in [Memory](16-memory.md).

| Key | Default | Purpose |
|---|---|---|
| `memory` | `1` | Master switch. `0` stops both recall and learning; stored memories are kept. |
| `memory_engine` | `native` | Backend: `native` (built-in SQLite with BM25 + vector RRF) or `mem0` (the Mem0 library with graph/vector management). Set by `/memory engine <name>`. Default `native`; the installers and `--memory-setup` switch it to `mem0`. |
| `memory_db_path` | `~/.agent8088/memory.db` | The store for the native engine. One SQLite file, mode `0600`. |
| `memory_user_id` | `owner` | Whose memories these are. One identity by default, so memory carries across the CLI and every gateway platform. |
| `memory_scope_by_identity` | `0` | `1` gives each gateway identity its own namespace. Needed only if a `*_allowed_users` line holds more than one person. |
| `memory_embed_model` | `nomic-embed-text` | Embedding model for semantic recall (274 MB; the installers pull it). Missing or unreachable degrades recall to keyword-only rather than failing. |
| `memory_embed_provider` | `ollama` | Provider asked for embeddings. Deliberately **not** your chat provider — chat and embeddings are separate services, and the default embed model is an Ollama model. Whatever serves chat is irrelevant here. |
| `memory_embed_timeout_seconds` | `30` | Timeout for an embedding request, in seconds (minimum `1`). Increase for a slow local embedding service; unavailable embeddings still degrade to keyword-only selection. |
| `memory_extract_model` | *(chat model)* | Model for the fact-extraction call. Point at something small to spend less. |
| `memory_extract_max_tokens` | `4000` | Output budget for the fact-extraction call (minimum `256`). Reasoning models need room to think before they answer. |
| `memory_capture` | `1` | `0` keeps recall and stops learning new facts. |
| `memory_recall_limit` | `5` | Facts injected into a turn's prompt. |
| `memory_rrf_k` | `60` | RRF damping constant. Lower makes rank 1 dominate; higher flattens. |
| `memory_min_score` | `0` | Drop fused hits below this score. |
| `memory_max_per_turn` | `10` | Cap on facts one turn may create. |
| `memory_notifications` | `on` | What you see when a turn learns something: `off` silent, `on` a one-line count, `verbose` the facts themselves plus a line on turns that stored nothing. `/memory notify` changes it live. |

The `mem0` engine reads its own keys (all with working defaults, so switching
engines is enough):

| Key | Default | Purpose |
|---|---|---|
| `memory_mem0_vector_store` | `qdrant` | Vector store backend (embedded local Qdrant). |
| `memory_mem0_dir` | `<data dir>/mem0` | Mem0 data directory. An explicit value wins, same as `memory_db_path`. |
| `memory_mem0_llm_provider` / `memory_mem0_llm_model` | active provider / chat model | Model Mem0 uses for its own extraction calls. |
| `memory_mem0_embed_provider` / `memory_mem0_embed_model` | `memory_embed_provider` / `memory_embed_model` (default `ollama` / `nomic-embed-text`) | Embedding pair for Mem0; falls back to the native keys above, so both engines share one Ollama embedding model. |

Only what **you** type can become a memory or trigger a recall — tool output
never does — and a recalled memory can never authorise a tool call.

## Limits

| Key | Default | Purpose |
|---|---|---|
| `max_read_bytes` | `2 MB` | Cap on a single file read. |
| `tool_result_max_chars` | `3000` | Characters of one tool result shown to the model. The rest stays reachable with `read_content`. |
| `read_content_window_chars` | `2400` | Characters `read_content` returns per call. |
| `reuse_known_verdicts` | `1` | Reuse a recorded check or test result while nothing has changed since it ran. `0` always reruns. |
| `max_http_bytes` | `5 MB` | Cap on an HTTP response. |
| `max_tool_output_bytes` | `1 MB` | Cap on tool output fed back to the model. |
| `max_tool_timeout_seconds` | `1200` | Hard ceiling for one tool call. |
| `compaction_threshold_pct` | `75` | Automatically summarize older messages at this estimated context usage (`0` disables). |
| `compaction_cooldown_turns` | `6` | Turns to wait after a successful automatic compaction before looking again, unless the context is already above 90% of the window. `0` removes the wait. |
| `compaction_summary_max_chars` | `16000` | Longest the carried-over conversation summary may grow. Compaction keeps an earlier summary exactly as written and summarizes only what came after it, so a long run's first facts are not blurred by repeated rewriting; past this size the oldest summary sections are dropped whole, never rephrased. Each summary ends with a short harness record the model does not write (files written this run, verified checks), rebuilt from live state at every compaction. |
| `compaction_constraint_chars` | `1500` | Room in a summary for the person's rules, copied word for word: rule sentences from their own messages and from rule-like text files inside the working folders they explicitly ask to follow, obey, apply, comply with or adhere to (for example, `Follow policies/rules.md`). Merely naming or asking to read/audit a file does not authorize its contents. Resolved file paths survive later compactions; file excerpts remain user-level context, not system policy or authority for additional actions/tools. Tool output, web pages, ordinary documents and credential files are not copied. When rules do not all fit, the newest are kept. |
| `diagnostic_after_failures` | `2` | After this many identical failures in a row (same tool, same error code), the model is shown a read-only check of where commands run: the working-directory status, a short listing, the user, free disk space and which interpreters exist. The same check is shown before the agent accepts an answer that says the environment is unavailable. Minimum `2`. |
| `max_image_bytes` | `20 MB` | Cap on an image attachment. |
| `document_process_concurrency` | `4` | Document chunks `document_read action=process` keeps in flight (clamped 1-16). Chunk requests are independent, so this multiplies throughput almost linearly until the provider rate-limits. |
| `browser_max_steps` | `25` | Max steps `browse_page`'s browsing agent takes on one task. Override once with `AGENT8088_BROWSER_MAX_STEPS`. |
| `browser_task_timeout_seconds` | `600` | Overall wall-clock limit for one `browse_page` call. Clamped to `max_tool_timeout_seconds`; override once with `AGENT8088_BROWSER_TASK_TIMEOUT_SECONDS`. |
| `browser_screenshots` | `0` | Enable browser screenshots for a vision-capable model; override once with `AGENT8088_BROWSER_SCREENSHOTS=1`. |
| `browser_max_actions_per_step` | `1` | Actions the browsing model may batch into one step. Raise for form-heavy sites; keep at 1 where clicks reshuffle the DOM. |
| `browser_headless` | `1` | `0` opens a visible window (demos, debugging a stuck selector). Visibility only — every guard is unaffected. |
| `browser_window_size` | (unset) | Visible browser window size as `W,H`. Only used with `browser_headless=0`. |
| `browser_window_position` | (unset) | Visible browser window position as `X,Y`, so it does not land on top of the terminal. Only used with `browser_headless=0`. |
| `browser_reuse_session` | `1` | Keep the browser alive across consecutive tasks in the session, preserving cookies and in-memory tabs. |
| `browser_hitl` | `1` | Human-in-the-loop interrupts (`ask_human`) for captchas, passwords and user input. |
| `browser_flash_mode` | `0` | Much smaller system prompt (~2.4 KB vs ~22 KB) and leaner action schema — each step 2-4x faster on a small model. Off by default: A/B it on your model first. |
| `browser_structured_output_mode` | `auto` | `auto` tries JSON schema then JSON object; `json_object` skips the first attempt — set it for a provider known to ignore schemas (e.g. Ollama Cloud serving GLM). |
| `browser_llm_max_tokens` | `4096` | Starting completion cap for the browsing model's own calls. Adaptive: doubles when cut off, decays toward ~1.5x actual use. |
| `browser_llm_min_tokens` | `1024` | Floor for the adaptive cap. |
| `browser_llm_max_completion_tokens` | `16384` | Ceiling for the adaptive cap. |
| `browser_llm_extra_body` | (empty) | JSON fields sent only to browser-use model calls; use `{"chat_template_kwargs":{"enable_thinking":false}}` for Qwen served by vLLM. |

### Dynamic turn budget

`max_turns` is the starting allowance, not a fixed hard cap. The default is
**150 rounds**, shared by the CLI, Web UI and messaging gateways. Set
`max_turns` in `config.txt` or use `/maxturns <int>` to override it.
Older saved sessions without this setting retain the current value on resume.

At a block boundary, growth requires more than half the block's rounds to
produce fresh, successful work. Extensions add another block of rounds up to
`max_turns x dynamic_turns_ceiling_multiplier` (450 at the defaults).
Repetition and failed calls do not earn extensions.

The signals come from state the loop already tracks, so extension costs no
extra tokens and the model cannot request rounds it has not earned: a run that
starts repeating itself, or whose steps start failing, simply stops earning
them and ends where it is.

| Key | Default | Purpose |
|---|---|---|
| `max_turns` | `400` | Starting rounds per request. Time, token and cost budgets are separate limits; raising this does not raise those limits. |
| `dynamic_turns_ceiling_multiplier` | `3` | Hard turn ceiling as a multiple of `max_turns` (default 400 x 3 = 1200 rounds). `1` disables growth and restores a fixed limit. |
| `dynamic_turns_extension` | `5` | Rounds granted per extension. |
| `stall_rounds` | `6` | Rounds in a row that bring nothing new (no new call that worked, no file changed, no check newly passing, no part finished) before the main agent is asked once to finish with what it has or change approach. A second stretch of the same length with nothing ends the run with the usual summary. Reading or fetching something new always counts as progress. `0` turns the watch off. |
| `repeat_refresh_seconds` | `20` | Refresh a consecutive identical, successful shell observation after this many seconds, at most 12 refreshes per command per request. Only supported read-only probes qualify; writes, unknown commands and failures stay cached. Explicit supported waits can refresh sooner but share the same cap. `0` disables timed refresh, not explicit waits. |
| `verification_loop_rounds` | `8` | Once the work is built, its checks pass and no part is open, this many rounds in a row that change no file are answered once with a message that finishing is the next step (at most twice per request). It never ends the run. `0` turns it off. |

Growth applies to the top-level request and to delegated sub-agents. It does
**not** apply to the auditor (verification is bounded work, and letting it grow
inflates the share of a request spent checking rather than doing), to anything
nested deeper than one sub-agent, to single-round calls, or to durable-task
slices — a slice already re-enters the agent up to `max_slices` times, so
growth there would multiply rather than extend.

When a request does exhaust its ceiling, it spends one final round with no
tools available, asking the model to answer from what it already has. The user
gets the partial result and a note that the budget ran out, rather than an
error and a raw tool dump. If that round fails, the plain error report is
returned instead.

### Turn budget

`max_turns` bounds how many *rounds* a request takes. These bound what those
rounds may consume — a plan or subagent chain can burn a lot inside a small
number of rounds. All default to `0`, meaning disabled.

| Key | Default | Purpose |
|---|---|---|
| `max_turn_seconds` | `0` | Wall-clock ceiling for one request. When set, the main agent is told at 60% and again at 85% of it how long is left, and the second message asks for a final reply that says what is done, what is not done and what could not be verified. Nothing is announced when it is `0`. The round limit gets the same two warnings (80% and 95%) against `max_turns` × `dynamic_turns_ceiling_multiplier`, the bound a run cannot pass; bounds under 20 get none. |
| `max_post_check_rounds` | `5` | Tool rounds with no file or state change allowed after a finishing check or a successful test of changed work before the model is told to answer; a fresh summary is requested 2 rounds later. A write resets the count. Research and failed tests do not start it. `0` disables it. |
| `max_completion_continuations` | `3` | Follow-ups the completion checks may send in one request (empty reply, reply admitting unfinished work, a named file missing, a part of a multi-part request skipped, changed work unverified). A follow-up that makes progress hands its slot back. When spent the answer is accepted with a "Not completed" note. `0` turns the checks off. See [architecture](11-architecture.md#completion-checks). |
| `requirement_evidence` | `1` | Check the parts of a request against what the run delivered. A paragraph that states three or more requirements ("it should ...", "users must be able to ...") builds a checklist like a numbered list does, and a part that asks for something to be built counts as done only when the files the run wrote show it (key words found in their text, with camelCase and snake_case split), not when the reply says so. A part that only asks the agent to tell the user something is still met by the reply. Parts left over cost a follow-up within `max_completion_continuations` and appear in the answer's "Not completed" note. `0` restores the reply-only check and the list-only checklist. |
| `completion_smoke_test` | `report` | What to do with a failed [`check_app`](04-tools.md#check_app) smoke test of a page or script the run wrote: `report` shows it in the answer and never blocks, `enforce` also asks the model to fix it, `off` skips it. |
| `completion_smoke_timeout_seconds` | `20` | Time allowed for one page check. |
| `smoke_click_through_steps` | `12` | After the first control, the page check clicks this many more visible controls (outermost first, "back/quit/close/reset" last) within half of `completion_smoke_timeout_seconds`, and fails on a page error those clicks raise, naming the control. Stops when the page leaves its address. `0` clicks only the first control. |
| `completion_smoke_max_failures` | `2` | Failed smoke tests per request that `enforce` sends back to the model before accepting the answer with a note. |
| `grounding_check` | `1` | A request to build something that must follow real rules or data ("valid and available in X", "official", "full roster") and that names its subject, with no facts supplied (no URL, file or attachment), gets one follow-up if the run wrote files without a single search, browsed page or fetch: look up what you relied on, fix what differs, name the sources. Needs `web_search` to be available. Spends one `max_completion_continuations`. `0` turns it off. |
| `initial_completion_cap` | `8192` | Ceiling for the completion-token cap of an ordinary call, bounded by the provider's limit. A call that runs away (an unclosed tool call, a text loop) stops here instead of using the full limit. A cut-off tool call earns a larger cap on its retry (up to `write_output_cap`); plain-text loops go to the final no-tools round after two cut-offs. `0` always uses the provider's full limit. |
| `salvage_cutoff_writes` | `1` | When a `write_file` call is cut off mid-content, save the content up to its last whole line if that is safe (a new file, or an append part; never an overwrite of an existing file, never `.json`), once per resolved file, through the normal write path. The file is marked unfinished: completion checks request continuation or report it as incomplete if the run reaches its limits. Only a successful write to that same file clears the marker. The retry asks for the rest with `mode="append"`. `0` discards the partial call as before. |
| `adaptive_completion_cap` | `1` | With the ceiling above, cap an ordinary call at twice the median size of the last five normal replies (reasoning included), never below `main_llm_min_tokens` (4096). A runaway call then stops after a few minutes instead of the full ceiling. `0` keeps the fixed ceiling. |
| `main_llm_min_tokens` | `4096` | Floor for the adaptive completion cap and for the cap earned by a cut-off retry, bounded by the provider's limit. |
| `length_retry_max_tokens` | `0` | Additional ceiling for a cut-off retry's completion cap. `0` leaves the other configured and provider limits in effect. |
| `length_cutoff_max_retries` | `4` | Consecutive length cut-offs before one final no-tools round reports the incomplete output. `0` disables this retry-count stop; other budgets still apply. |
| `failing_check_follow_up` | `1` | When a reply would end the work while the latest automated check (a test run or an audit) is still failing, send up to two follow-ups per request, each naming the failing command and how it failed: fix it and run it again, or say plainly that it still fails. The answer's "Incomplete" note is unchanged. Each follow-up spends one `max_completion_continuations`. `0` keeps the note only. |
| `not_verified_headline` | `1` | When the run itself saw a clear sign that the work is unfinished or not working (a failed page or script check, an unresolved failing check, parts of the request not addressed, a requested file missing, a file cut off, a credential written in clear text), the answer starts with one line "NOT VERIFIED: ..." naming it; the notes at the end are unchanged. The common notes on correct work (no fresh verification evidence, no tests written, smoke test unavailable) never trigger it. `0` turns it off. |
| `write_output_cap` | `16384` | Largest cap a cut-off tool call (a file being written) may earn on its retry. Beyond this, write the file in parts with `write_file mode=append`. `0` removes the ceiling (the provider's limit still applies). |
| `thinking_off_sticky_calls` | `3` | After a cut-off whose whole budget went to reasoning (no answer, no tool call), this many following ordinary calls also run with thinking off, at the normal cap, so a file the model drafted in its thinking is written straight into the tool call. Thinking returns to normal afterwards. `0` keeps only the one retry. |
| `reasoning_budget_tokens` | `0` | Tokens a model may spend thinking on an ordinary turn, where the provider can be told (see `provider.<name>.reasoning_budget_extra_body`). `0` leaves the provider's default. Prefer this to raising the output cap: a larger cap makes every runaway call proportionally longer without making an answer more likely. |
| `plan_mode_timeout_seconds` | `600` | Default wall-clock ceiling for the planning phase when `max_turn_seconds` is unset. It ends when the plan is approved; the work that follows is limited only by `max_turn_seconds` (off by default). |
| `plan_mode_retry_limit` | `4` | Invalid mutation attempts allowed before plan mode stops safely. |
| `max_turn_tokens` | `0` | Token ceiling (input + output) for one request. |
| `max_turn_cost_usd` | `0` | USD ceiling. Needs the two price keys below. |
| `cost_per_1k_input` | `0` | Input token price, for the cost ceiling. |
| `cost_per_1k_output` | `0` | Output token price, for the cost ceiling. |

When a budget trips, the request stops at the start of the next round — before
the model call, so an exhausted budget costs nothing — and returns the partial
result along with the name of the key to raise.

Subagents inherit the parent's budget. A fresh budget per subagent would be a
free bypass: delegate, and the limit starts over.

On streaming responses the provider returns no usage object, so tokens are
estimated at roughly 4 characters each. That still bounds a runaway loop; it is
just less precise than the non-streaming path.

### Write blast radius

The permission layer decides *whether* a write is allowed. These bound *how many*
and *how big* — a model looping on `write_file` inside an already-approved turn
is a plausible accident that the permission gate does not catch. Both default to
`0` (disabled).

| Key | Default | Purpose |
|---|---|---|
| `max_writes_per_turn` | `0` | Files one request may write. The parts of one file written with `write_file mode=append` count as one. |
| `file_backups` | `1` | Copy a file's original bytes aside before the first change to it in a session. See [Backups of overwritten files](04-tools.md#backups-of-overwritten-files). |
| `file_backup_max_file_mb` | `10` | Largest single file that is backed up (larger files are skipped). |
| `file_backup_max_mb` | `200` | Total backed up per session; past it, further backups are skipped. |
| `file_backup_keep_days` | `14` | Backup sessions older than this are deleted (`0` keeps them all). |
| `max_write_bytes` | `0` | Bytes a single write may contain. For a file written in parts, the bytes of all its parts this request. |

Checked *before* the permission gate, so the refusal is not something a user can
wave through by mistake, and reset only by the outermost request so a subagent
cannot hand itself a fresh write budget.

### Plan step auditing

With `plan_audit=1`, every mutating `execute_plan` step is verified against the
real environment by the readonly `auditor` sub-agent before the plan moves on,
and a failed verification halts the plan. Off by default: it costs one extra
model call per mutating step, drawn from the same turn budget as the work
itself. Worth enabling for unattended runs (gateway, cron), which is exactly
where a silently half-done plan does damage.

| Key | Default | Purpose |
|---|---|---|
| `plan_audit` | `0` | `1` turns on step verification. `/audit` shows and toggles it. |
| `plan_audit_revert` | `1` | With auditing on, a write that **fails** verification is restored to its exact pre-step bytes: only verified state persists. |
| `plan_audit_confirm_fail` | `1` | A tool or plan-step audit failure needs a second failing auditor before rollback. Disagreement is reported as inconclusive and leaves the change in place. |
| `plan_audit_revert_max_bytes` | `1048576` | Files larger than this are not snapshotted, so they are not reverted — the plan output says so rather than implying otherwise. |
| `plan_audit_max_per_turn` | `12` | Verification calls one top-level turn may spend. `0` stops verifying without turning the feature off. |
| `plan_audit_timeout_seconds` | `120` | Wall clock for one auditor sub-run. |

## Code review

`/review` runs OpenCodeReview locally over a diff and prints findings; see
[Open code review](../open-code-review.md). `open_code_review_enabled=0` makes
`/review` say the engine is disabled instead of running.

| Key | Default | Purpose |
|---|---|---|
| `open_code_review_enabled` | `0` | `1` after the installer provisions the engine; `/doctor` reports readiness. |
| `open_code_review_executable` | (installed path) | The native `opencodereview` binary. Written by the installer; set it only for a custom install. |
| `open_code_review_mode` | `auto` | `native` runs the engine as the reviewer, `delegated` prepares scope and hands the review to this agent, `auto` picks native when credentials exist. |
| `open_code_review_timeout_seconds` | `1800` | Wall-clock ceiling for one native review. Clamped to 3600. |
| `open_code_review_token_budget` | `500000` | Token budget for one native review (50 000-2 000 000). |
| `open_code_review_max_tools` | `50` | Tool rounds the reviewer may take (50-200). |
| `open_code_review_concurrency` | `8` | Parallel file reviews (1-16). |
| `open_code_review_file_timeout_minutes` | `15` | Per-file ceiling (1-60). The lever to raise on a slow endpoint — lowering the token budget or reviewing a smaller range are the alternatives. |

## Display, logs and local data

| Key | Default | Purpose |
|---|---|---|
| `syntax_theme` | `monokai` | Pygments style for code and diffs in the terminal. |
| `log_enabled` | `1` | `0` turns off the JSONL log under `<data dir>/logs`. |
| `log_level` | `INFO` | Level for that log: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `searxng_port` | `8080` | Host port for the local SearXNG container that `/search setup` provisions. |
| `task_db_path` | `tasks.db` beside `config.txt` | SQLite store for long-running tasks. |

## Extension points

| Key | Purpose |
|---|---|
| `tools_file` | Path to `tools.txt` (the tool registry). |
| `system_file` | Path to `system.md` (base prompt). |
| `banner_file` | Custom startup banner. |
| `user_file` | Path to `USER.md` (persona). |
| `skills_dir` / `agents_dir` | Skill packages and bundled sub-agent profiles. |
| `user_agents_dir` | Your own sub-agent profiles, merged over `agents_dir`. Defaults to `%LOCALAPPDATA%\agent8088\agents` (POSIX: `~/.agent8088/agents`) so they survive an upgrade. |
| `subagent_max_depth` | Recursion limit for `spawn_subagent`. |
| `default_subagent` | Profile used when none is named. |
| `max_subagent_answer_chars` | Cap on a sub-agent's returned answer (default `6000`, `0` disables). A sub-agent exists to keep work out of the parent's context, so an unbounded answer defeats the delegation. Truncation is marked in the text, never silent. |
| `subagent_max_turns.<profile>` | Rounds one sub-agent profile may take. **Overrides the profile's own frontmatter.** |
| `subagent_max_seconds.<profile>` | Wall clock, in seconds, for one sub-agent profile. **Overrides the profile's `max_seconds`.** Without either, `subagent_timeout_seconds` (300) applies. |
| `tool_timeout.<tool>` | Seconds one tool may run. **Overrides the inline `timeout=` in `tools.txt`.** |

### Changing a limit without editing this file

`/limits` shows every limit and sets any of them, writing both the running
process and this file so the change survives a restart:

```
/limits                              # show everything
/limits max_turn_seconds 60
/limits subagent explore 12
/limits tool browse_page 90
/limits provider glm context_window 1048576
/limits provider custom max_completion_tokens 32768
```

Raising a limit is allowed and reported as such, with a further warning past a
recommended ceiling:

```
⚠ raised max_turn_seconds: 60 → 5000
  above the recommended 900 — one request can now run a long way before
  anything stops it.
```

Two of these keys deliberately outrank the files they duplicate — a runtime
override that lost to `tools.txt` or to profile frontmatter on the next start
would be a setting that only appeared to work.

The always-on floor is not on this list and no value reaches it: credential
files, shell startup files and destructive git stay refused whatever the
numbers say.

> To remove a built-in tool, comment out its line in `tools.txt` — see
> [Disabling a built-in](04-tools.md#disabling-a-built-in). There is no
> `disabled_tools` key; an earlier version of this page documented one that was
> never implemented.

## API keys and the `.env` store

Keys and gateway tokens live in `~/.agent8088/.env`, **not** `config.txt`:

```ini
# ~/.agent8088/.env   (mode 0600)
OPENAI_API_KEY=sk-...
SLACK_BOT_TOKEN=xoxb-...
```

`config.txt` only points at them:

```ini
provider.openai.api_key_env=OPENAI_API_KEY
```

**Migration is automatic and one-time.** On first run, any literal
`provider.*.api_key`, `*_bot_token` or `*_app_token` in `config.txt` is moved
into `.env`, the literal is removed, and an `*_env` pointer is written. It is
idempotent — it will not re-run or lose a key. You'll see:

```
[agent8088] Migrated 2 keys to /home/you/.agent8088/.env
```

### Resolution order

For a provider key, most explicit first:

1. the `.env` key store
2. an explicit `api_key` in `config.txt`
3. `os.environ`

`os.environ` is deliberately **last** so a stray shell export (e.g.
`OPENAI_API_KEY` set for another tool) cannot silently redirect a configured
provider.

Values from any of these sources are redacted from tool output and from the
model's answers, so `cat config.txt` cannot exfiltrate them.

## Environment variables

| Var | Purpose |
|---|---|
| `AGENT8088_CONFIG` | Override the config path. |
| `AGENT8088_HOME` | Override the data directory. |
| `AGENT8088_PROVIDER` | Override the active provider. |
| `AGENT8088_PERMISSION` | Starting permission mode. |
| `AGENT8088_SANDBOX` | Override the sandbox backend. |

Setting `AGENT8088_CONFIG=/nonexistent` forces packaged defaults — this is how
the test suite stays hermetic.


### Recovery limits and fallback diagnostics

Each completion is fitted to the selected provider/model's context window and
output ceiling. A fallback with insufficient prompt headroom is skipped before
sending a request. Switching models does not carry the first model's output
limit into the next request.

`max_turn_seconds` applies between retry, fallback, automatic escalation and
context-repair attempts. Request timeouts and backoff waits use the remaining
time. A response arriving after expiry cannot execute its tool calls.

A 401/403 can try a configured independent provider once. Targets using the
same rejected credential or the same provider are skipped. A dropped stream
gets one primary reset, then may switch to a configured fallback with its
partial presentation reset. Incomplete tool calls never execute.

Fallback entries are trimmed and deduplicated in order. Missing `provider:model`
syntax, unknown providers and empty models are reported at startup and in
`/doctor`; correcting the setting clears its old warnings.

Repeated browser tasks and opaque CLI-Anything commands reuse their result for
the whole request, including uncertain timeout results. An intervening read does
not authorize replay. Inspect external state with a different observation task
and ask for a new instruction when replay is needed. Built-in read-only
CLI-Anything status/list/search/info/skill queries can refresh; arbitrary
harness commands are not assumed read-only based on their names.
