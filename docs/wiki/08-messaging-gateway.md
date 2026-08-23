# Messaging Gateway

[← Wiki index](README.md)

Run Agent8088 as a bot in Slack, WhatsApp, Discord, Email or Telegram. Same
engine, same tools, same permission layer — approvals just happen in chat
instead of a terminal.

```sh
agent8088 --gateway-setup     # configure a channel
agent8088 --gateway           # run it
```

Requires the `gateway` extra:

```sh
pip install -e ".[gateway]"
```

## One channel at a time

`--gateway-setup` is a single-select picker: choosing Slack disables WhatsApp,
Discord, Email and Telegram. This is deliberate — one agent identity per
running gateway keeps session keys and approvals unambiguous.

## Access control — fail closed

Nobody can talk to the bot unless listed:

```ini
slack_allowed_users=U01ABC2DEF3,U02GHI4JKL
discord_allowed_users=123456789012345678
whatsapp_allowed_users=+15551234567
telegram_allowed_users=123456789,987654321
```

An **empty list denies everyone.** `*` allows anyone (use with care).

### Rate limiting

Being allowlisted is not a licence to flood. Every turn serializes behind one
global lock, so one user sending in a loop starves everyone else in the queue:

```ini
gateway_rate_limit_per_min=10     # default 20; 0 disables
```

Counted per user over a sliding 60-second window, **slash commands included** —
otherwise `/help` would be a free flood channel. Over the limit, the bot replies
once and drops the message. Rejected messages are not counted toward the window,
so a user who keeps hammering still drains out of it rather than being locked out
permanently. Drops are recorded in the [audit log](03-permissions-and-security.md#audit-trail)
when it is enabled.

### Inbound text is sanitized

Chat-template control tokens are stripped from every inbound message before the
model sees it. Without that, a message containing `<|im_start|>system` is
tokenized as a real role boundary by self-hosted ChatML/Llama templates — a plain
WhatsApp message could forge a system turn and grant itself a permission mode.

The message is *not* demoted to untrusted data: the sender is allowlisted and is
the principal for that request, so wrapping their whole message in
"never instructions" markers would stop the gateway from acting at all.

### Ids are scoped to their platform

An id under `slack_allowed_users` is a *Slack* id. If it shows up on Discord,
the bot denies it. This prevents a user permitted on one platform from gaining
access through another platform's gateway.

```
denied 99887766 on discord: it is listed under slack_allowed_users, not
discord_allowed_users (strict_platform_allowlist is on)
```

For a short migration only, set the compatibility option below. It permits a
misplaced id and logs the configuration line to correct; remove it after moving
the id to its platform-specific allowlist:

```ini
strict_platform_allowlist=0
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

## Asking the bot what it can do

```
/capabilities
```

Reports the live tool list, connected MCP servers and their state, skills,
subagents, permission mode, sandbox backend, and which guardrails are active.
Asking in plain language ("what tools do you have?") gets the same answer — the
agent calls `describe_capabilities` itself.

## Recommended hardened profile

For any gateway that is not a single-user toy:

```ini
audit_log=1
gateway_rate_limit_per_min=10
max_turn_tokens=60000
max_turn_seconds=300
max_writes_per_turn=20
blocked_domains=pastebin.com,transfer.sh,file.io,0x0.st
strict_platform_allowlist=1
denial_breaker_threshold=3
```

`audit_log=1` matters most here: the gateway is the multi-user surface, and it is
the only place you get a durable record of who asked for what and what was
refused. See [Permissions & Security](03-permissions-and-security.md#audit-trail).

## Platform specifics

| | Slack | WhatsApp | Discord | Email | Telegram |
|---|---|---|---|---|---|
| Transport | Socket Mode (outbound WS, no public URL) | local Baileys bridge (Node.js) | `discord.py` gateway | IMAP poll + SMTP | `python-telegram-bot` long polling |
| Streaming | ✅ | ✅ | ✅ | ❌ | ✅ |
| Message cap | 39,000 chars | 4,096 | 2,000 | — | 4,096 |
| Threads | ✅ `thread_ts` | ❌ | ❌ | ✅ `In-Reply-To` | ✅ `message_thread_id` |
| Approval UI | text | text | **buttons** | text | text |
| Markdown | `markdown_to_slack()` | `markdown_to_whatsapp()` | `markdown_to_discord()` | plain text | `markdown_to_telegram()` |
| Dedup | by `ts`, 500-entry cap | by message id | by message id, 500-entry cap | by UID | by `update_id`, 500-entry cap |

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

### Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather) -- `/newbot`, copy the
   API token (looks like `123456789:ABCdef...`)
2. For group chats, disable **privacy mode** via BotFather or promote the bot
   to group admin. Remove and re-add the bot to any group afterwards.

```ini
telegram_enabled=1
telegram_allowed_users=123456789
```

The token lives in `~/.agent8088/.env` as `TELEGRAM_BOT_TOKEN`.

DMs are always accepted; in groups/supergroups it **requires an @mention of
the bot or a reply to one of its messages**. The @mention is stripped from
the text before the model sees it.

### Email

Polls IMAP for new mail and replies over SMTP, so any mailbox works — no bot
registration and no platform app to create.

```ini
email_enabled=1
email_allowed_users=you@example.com,colleague@example.com
email_smtp_port=587      # optional, this is the default
email_imap_port=993      # optional, this is the default
email_verify_sender=1    # optional, on by default
```

Credentials live in `~/.agent8088/.env`, never in `config.txt`:

```
EMAIL_ADDRESS=you@gmail.com
EMAIL_PASSWORD=app-specific-password
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_IMAP_HOST=imap.gmail.com
```

Use an app-specific password, not your account password — Gmail and most
providers require one anyway once 2FA is on.

Replies thread against the sender's original subject and `Message-ID`, so a
conversation stays in one mail thread rather than starting a new one each turn.

`email_verify_sender=1` (the default) reads the `Authentication-Results` header
and requires `dmarc=pass`, `spf=pass` or `dkim=pass` before the allowlist is
even consulted — authentication runs *first*, because a `From:` header on its
own is trivially forgeable. Turning it off means anyone who can spoof a `From:`
line matching `email_allowed_users` can drive your agent.

It **fails closed**: a message with no `Authentication-Results` header at all is
rejected. If your mail server does not add that header, every message is
silently dropped — that is the setting to look at first when the bot receives
nothing. Unauthorized mail is discarded without a reply either way, so silence
is the expected symptom rather than an error.

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
TELEGRAM_BOT_TOKEN=...
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
