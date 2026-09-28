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
| `project_root` | cwd | Base for relative paths. |
| `shell_cwd` | cwd | Working directory for shell commands. |
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
| `provider.<name>.context_window` | Context window for this provider profile. Overrides the global value. |
| `provider.<name>.max_completion_tokens` | Maximum output tokens for this provider profile. Overrides the global value. |
| `provider.<name>.temperature` | Sampling temperature for one provider; overrides the session value, including `/temp`, on that provider's requests. Useful when a server expects a particular sampling setting. |
| `provider.<name>.extra_body` | JSON object sent as additional request fields for that provider, such as vLLM `chat_template_kwargs`. Invalid JSON is ignored with a warning. Only configure fields your server accepts. |
| `fallback_models` | Comma-separated `provider:model` chain, tried on 429/503/connection errors. |
| `tool_selection` | Which tool schemas go on the wire for native function-calling. `hybrid` (default) sends the ~10 most relevant via keyword + embedding retrieval, falling back to `full` when the two signals disagree or embeddings are unavailable; `full` always sends every schema; `auto` enables hybrid only for the measured `provider:model` entries in `tool_selection_models`. `/tool-selection <mode>` changes it live. |
| `tool_selection_models` | Comma-separated `provider:model` entries `tool_selection=auto` treats as measured-safe for hybrid mode. |
| `context_window` | Token budget for history trimming. |
| `max_completion_tokens` | Global output-token ceiling when the active model has no reviewed limit or provider override. |
| `timeout_seconds` | Per-request timeout (default `120`). |

Transient provider failures are retried before the turn fails or `fallback_models`
takes over:

| Key | Default | Purpose |
|---|---|---|
| `api_max_retries` | `3` | Attempts per call on retryable errors (429/503/connection). `0` disables. |
| `api_retry_initial_delay_ms` | `500` | First backoff delay. |
| `api_retry_max_delay_ms` | `10000` | Ceiling for the exponential backoff. |
| `api_retry_jitter_ratio` | `0.1` | Random jitter added to each delay (0-1), so parallel retries do not sync up. |

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
| `web_search_max_per_turn` | `6` | Searches one request may run before the model must answer from what it has. `0` removes the cap. |
| `ssrf_allow_private` | `0` | `1` opens the entire private network. Prefer the allowlist. |
| `allowed_domains` | (empty) | If set, the **only** public hosts the agent may reach. Empty means all are reachable. |
| `blocked_domains` | (empty) | Public hosts the agent may never reach. Wins over `allowed_domains`. |
| `max_command_chars` | `16384` | Commands longer than this are refused rather than analysed. |
| `audit_log` | `0` | `1` appends one redacted JSON line per gated tool decision. Turn this on for any gateway deployment. |
| `audit_log_path` | `<data dir>/audit.jsonl` | Where the audit trail is written (mode 0600). |
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
| `sandbox_allowed_domains` | (empty) | Domains reachable from inside the sandbox. |
| `docker_image` / `docker_network` | `python:3.11-slim` / `none` | Docker fallback settings. |
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
| `memory_extract_model` | *(chat model)* | Model for the fact-extraction call. Point at something small to spend less. |
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
| `max_http_bytes` | `5 MB` | Cap on an HTTP response. |
| `max_tool_output_bytes` | `1 MB` | Cap on tool output fed back to the model. |
| `max_tool_timeout_seconds` | `600` | Hard ceiling for one tool call. |
| `compaction_threshold_pct` | `75` | Automatically summarize older messages at this estimated context usage (`0` disables). |
| `max_image_bytes` | `20 MB` | Cap on an image attachment. |
| `document_process_concurrency` | `4` | Document chunks `document_read action=process` keeps in flight (clamped 1-16). Chunk requests are independent, so this multiplies throughput almost linearly until the provider rate-limits. |
| `browser_max_steps` | `25` | Max steps `browse_page`'s browsing agent takes on one task. Override once with `AGENT8088_BROWSER_MAX_STEPS`. |
| `browser_task_timeout_seconds` | `600` | Overall wall-clock limit for one `browse_page` call. Clamped to `max_tool_timeout_seconds`; override once with `AGENT8088_BROWSER_TASK_TIMEOUT_SECONDS`. |
| `browser_screenshots` | `0` | Enable browser screenshots for a vision-capable model; override once with `AGENT8088_BROWSER_SCREENSHOTS=1`. |
| `browser_max_actions_per_step` | `1` | Actions the browsing model may batch into one step. Raise for form-heavy sites; keep at 1 where clicks reshuffle the DOM. |
| `browser_headless` | `1` | `0` opens a visible window (demos, debugging a stuck selector). Visibility only — every guard is unaffected. |
| `browser_reuse_session` | `1` | Keep the browser alive across consecutive tasks in the session, preserving cookies and in-memory tabs. |
| `browser_hitl` | `1` | Human-in-the-loop interrupts (`ask_human`) for captchas, passwords and user input. |
| `browser_flash_mode` | `0` | Much smaller system prompt (~2.4 KB vs ~22 KB) and leaner action schema — each step 2-4x faster on a small model. Off by default: A/B it on your model first. |
| `browser_structured_output_mode` | `auto` | `auto` tries JSON schema then JSON object; `json_object` skips the first attempt — set it for a provider known to ignore schemas (e.g. Ollama Cloud serving GLM). |
| `browser_llm_max_tokens` | `4096` | Starting completion cap for the browsing model's own calls. Adaptive: doubles when cut off, decays toward ~1.5x actual use. |
| `browser_llm_min_tokens` | `1024` | Floor for the adaptive cap. |
| `browser_llm_max_completion_tokens` | `16384` | Ceiling for the adaptive cap. |
| `browser_llm_extra_body` | (empty) | JSON fields sent only to browser-use model calls; use `{"chat_template_kwargs":{"enable_thinking":false}}` for Qwen served by vLLM. |

### Dynamic turn budget

`max_turns` is what a request *starts* with, not what it is allowed. A run that
is still producing new, successful tool results has not failed, so ending it on
a number chosen before the task began throws away real work. When the loop is
one round from its soft limit and that round ran a new, non-repeated tool whose
result did not fail, it is granted another block of rounds — up to a hard
ceiling of `max_turns x dynamic_turns_ceiling_multiplier`.

The signals come from state the loop already tracks, so extension costs no
extra tokens and the model cannot request rounds it has not earned: a run that
starts repeating itself, or whose steps start failing, simply stops earning
them and ends where it is.

| Key | Default | Purpose |
|---|---|---|
| `dynamic_turns_ceiling_multiplier` | `4` | Hard turn ceiling as a multiple of `max_turns`. `1` disables growth and restores a fixed limit. |
| `dynamic_turns_extension` | `5` | Rounds granted per extension. |

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
| `max_turn_seconds` | `0` | Wall-clock ceiling for one request. |
| `plan_mode_timeout_seconds` | `300` | Default wall-clock ceiling used in plan mode when `max_turn_seconds` is unset. |
| `plan_mode_retry_limit` | `2` | Invalid mutation attempts allowed before plan mode stops safely. |
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
| `max_writes_per_turn` | `0` | Files one request may write. |
| `max_write_bytes` | `0` | Bytes a single write may contain. |

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
