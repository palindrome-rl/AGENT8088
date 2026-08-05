# Messaging Gateway

[← Wiki index](README.md)

Run Agent8088 as a bot in Slack, WhatsApp or Discord. Same engine, same tools,
same permission layer — approvals just happen in chat instead of a terminal.

```sh
agent8088 --gateway-setup     # configure a channel
agent8088 --gateway           # run it
```

Requires the `gateway` extra:

```sh
pip install -e ".[gateway]"
```

## One channel at a time

`--gateway-setup` is a single-select picker: choosing Slack disables WhatsApp and
Discord. This is deliberate — one agent identity per running gateway keeps
session keys and approvals unambiguous.

## Access control — fail closed

Nobody can talk to the bot unless listed:

```ini
slack_allowed_users=U01ABC2DEF3,U02GHI4JKL
discord_allowed_users=123456789012345678
whatsapp_allowed_users=+15551234567
```

An **empty list denies everyone.** `*` allows anyone (use with care).

### Ids are scoped to their platform

An id under `slack_allowed_users` is a *Slack* id. If it shows up on Discord,
the bot allows it but logs a one-time warning naming the fix:

```
allowing 99887766 on discord, but it is configured under slack_allowed_users —
move it to discord_allowed_users. This grace will be removed; set
strict_platform_allowlist=1 to enforce now.
```

The grace exists because ids can't realistically collide across platforms (a
Slack `U123ABC` vs a Discord snowflake vs a phone number), so hard-denying a
misconfigured-but-listed user would cause a silent outage for no security gain.
Unlisted ids are still denied. To enforce strictly now:

```ini
strict_platform_allowlist=1
```

## Permission mode

```ini
gateway_permission_mode=readonly   # default
```

`readonly` routes every mutation to a chat approval. Set `edit` to disable
prompts entirely (full-auto) — only sensible for a private, single-user bot.

## Approvals in chat

When a tool is blocked, the bot asks. Reply:

```
/approve     # allow this action
/deny        # refuse it
```

Discord additionally gets interactive **✅ / ❌ / ✔️ buttons**, whose timeout
is **fail-closed** — if nobody answers, the action is denied, not allowed.

## Platform specifics

| | Slack | WhatsApp | Discord |
|---|---|---|---|
| Transport | Socket Mode (outbound WS, no public URL) | local Baileys bridge (Node.js) | `discord.py` gateway |
| Streaming | ✅ | ✅ | ✅ |
| Message cap | 39,000 chars | 4,096 | 2,000 |
| Threads | ✅ `thread_ts` | ❌ | ❌ |
| Approval UI | text | text | **buttons** |
| Markdown | `markdown_to_slack()` | `markdown_to_whatsapp()` | `markdown_to_discord()` |
| Dedup | by `ts`, 500-entry cap | by message id | by message id, 500-entry cap |

### Slack

Create an app at [api.slack.com/apps](https://api.slack.com/apps):

1. **OAuth & Permissions** → scopes: `chat:write`, `app_mentions:read`,
   `channels:history`, `channels:read`, `im:history`, `im:read`
2. **Socket Mode** → enable, create an `xapp-` token
3. **Event Subscriptions** → `message.im`, `message.channels`, `app_mention`
4. **App Home** → enable the Messages tab
5. **Install App** → copy the `xoxb-` token

Socket Mode means no public URL or tunnel is needed.

**It only responds to DMs and @mentions** — not every message in a channel it's
in. It also ignores its own messages, so no feedback loops, and strips the
mention from the text before the model sees it.

### WhatsApp

Uses a local Node.js bridge (Baileys) — no Meta Business account.

```ini
whatsapp_mode=self-chat        # or: bot
whatsapp_session_dir=~/.local/share/agent8088/whatsapp/session
whatsapp_bridge_port=3000
```

- **`self-chat`** — only responds to messages from your own account. Good for a
  private assistant in your own "Message yourself" chat.
- **`bot`** — accepts from anyone; the Python allowlist gates access.

Pairing is a QR scan on first run. Re-pairing wipes the **entire** session
directory, because stale app-state-sync keys otherwise cause "failed to find
key" errors that silently block message receipt.

The bridge auto-restarts after 5 consecutive poll errors, and resolves opaque
WhatsApp LIDs back to phone numbers so allowlist matching works.

### Discord

1. Create an app at [discord.com/developers](https://discord.com/developers)
2. Enable the **Message Content** intent (required)
3. Copy the bot token, invite the bot to your server

DMs are always accepted; in guild channels it **requires an @mention**.

## Sessions

Per-chat history in `~/.agent8088/gateway-sessions/`, one JSON file per
conversation, keyed `agent:main:<platform>:<chat_type>:<chat_id>[:<thread>]`.
Keys are percent-encoded on disk because `:` is illegal in Windows filenames.

Threads get their own session, so a thread conversation stays separate from the
parent channel.

## Tokens

Gateway tokens live in `~/.agent8088/.env`, not `config.txt`:

```ini
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
DISCORD_BOT_TOKEN=...
```

Existing literal tokens in `config.txt` are migrated automatically on first run.
Re-running `--gateway-setup` and pressing Enter keeps the existing token rather
than blanking it.

## Architecture note

Every adapter implements one `BaseChannelAdapter` interface and the gateway
reuses `run_agent()` — the same engine core as the CLI and MCP server. Adapters
translate transport details; they do **not** re-implement permissions. That's
why a fix to the permission layer applies to all three platforms at once.

See [Architecture](11-architecture.md).
