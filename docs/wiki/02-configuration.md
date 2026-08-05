# Configuration

[← Wiki index](README.md)

`config.txt` is a flat `key=value` file with `#` comments — no YAML, no nesting.
Written with mode `0600`.

**Location**, in resolution order:

1. `$AGENT8088_CONFIG` if set
2. `~/.agent8088/config.txt`
3. `%LOCALAPPDATA%/agent8088/config.txt` (Windows)
4. the packaged `src/agent8088/config.txt` as a last-resort default

Secrets do **not** belong here — see [API keys](#api-keys-and-the-env-store).

## Paths and workspace

| Key | Default | Purpose |
|---|---|---|
| `allowed_paths` | `~` | Roots the agent may touch at all. Anything outside is refused before any other check. |
| `project_root` | cwd | Base for relative paths. |
| `shell_cwd` | cwd | Working directory for shell commands. |
| `no_prompt_paths` | (empty) | Writes here are auto-approved, no prompt. |
| `prompt_paths` | `.` | Writes here require per-action approval. |
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
| `fallback_models` | Comma-separated `provider:model` chain, tried on 429/503/connection errors. |
| `context_window` | Token budget for history trimming. |
| `timeout_seconds` | Per-request timeout (default `120`). |

Sampling: `frequency_penalty`, `presence_penalty` (temperature is a runtime
setting — `/temp`). Details in [Model Providers](05-model-providers.md).

## Security

| Key | Default | Purpose |
|---|---|---|
| `allowed_sensitive_files` | (empty) | Escape hatch — comma-separated names to exempt from the sensitive-file blocklist. |
| `deny_commands` | (empty) | Extra shell commands to refuse. |
| `readonly_safe_commands` | (built-in list) | Commands treated as safe inspection in readonly mode. |
| `ssrf_allow_hosts` | `127.0.0.1,localhost` | Internal hosts the agent may reach (e.g. a local SearXNG). |
| `ssrf_allow_private` | `0` | `1` opens the entire private network. Prefer the allowlist. |

## Sandbox

| Key | Default | Purpose |
|---|---|---|
| `sandbox_backend` | `auto` | `auto` → native, then Docker. `native`, `docker`, or `local` to force. |
| `sandbox_runtime_version` | pinned | Version of the native runtime to install. |
| `sandbox_allowed_domains` | (empty) | Domains reachable from inside the sandbox. |
| `docker_image` / `docker_network` | | Docker fallback settings. |

## Gateway

| Key | Default | Purpose |
|---|---|---|
| `slack_enabled` / `whatsapp_enabled` / `discord_enabled` | `0` | Enable a channel. Only one at a time via the wizard. |
| `slack_allowed_users` etc. | (empty) | Comma-separated user ids permitted per platform. **Empty means nobody** — fail-closed. |
| `strict_platform_allowlist` | `0` | `1` refuses an id listed under a *different* platform's line instead of allowing it with a warning. |
| `gateway_permission_mode` | `readonly` | `readonly` routes writes to chat approval; `edit` disables prompts. |
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

## Limits

| Key | Purpose |
|---|---|
| `max_read_bytes` | Cap on a single file read. |
| `max_http_bytes` | Cap on an HTTP response. |
| `max_tool_output_bytes` | Cap on tool output fed back to the model. |
| `max_image_bytes` | Cap on an image attachment. |
| `browser_timeout_ms` | `browse_page` timeout. |

## Extension points

| Key | Purpose |
|---|---|
| `tools_file` | Path to `tools.txt` (the tool registry). |
| `system_file` | Path to `system.md` (base prompt). |
| `user_file` | Path to `USER.md` (persona). |
| `skills_dir` / `agents_dir` | Skill packages and sub-agent profiles. |
| `disabled_tools` | Comma-separated tools to unregister. |
| `subagent_max_depth` | Recursion limit for `spawn_subagent`. |
| `default_subagent` | Profile used when none is named. |
| `banner_file` | Custom startup banner. |

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
