# Gateway Adapter Capability Comparison — Agent8088 vs Hermes Agent vs OpenClaw

**Date:** 2026-08-04
**Status:** Reference document
**Sources:** Agent8088 `src/agent8088/gateway/`, Hermes Agent `plugins/platforms/discord/`, OpenClaw `extensions/discord/src/`

---

## 1. Agent8088 — Current Adapter Capabilities

### BaseChannelAdapter (shared interface)

| Method | Purpose |
|---|---|
| `connect()` | Establish platform connection |
| `disconnect()` | Close connection |
| `send_message(chat_id, text, **meta) -> str` | Send text, return message ID |
| `edit_message(chat_id, msg_id, text)` | Edit an existing message |
| `on_message(event: MessageEvent)` | Inbound message hook |
| `supports_streaming() -> bool` | Whether adapter supports stream editing |
| `streaming_overflow_limit() -> Optional[int]` | Max chars per streamed message |
| `send_approval_prompt(chat_id, tool_name, reason, paths)` | Send approval prompt (default: plain text) |

### Slack Adapter

| Capability | Status | Notes |
|---|---|---|
| Text send/edit | ✅ | Via `chat_postMessage` / `chat_update` |
| Streaming | ✅ | `SlackStreamSink`, 39000 char limit |
| Threads | ✅ | `thread_ts` routing |
| Markdown conversion | ✅ | `markdown_to_slack()` |
| Dedup | ✅ | By message `ts`, 500-entry cap |
| Approval prompt | Text only | Uses base class default (no buttons) |
| Socket Mode | ✅ | Outbound WebSocket, no public URL |
| Mention handling | ✅ | Strips bot mention from text |
| File upload | ❌ | |
| Reactions | ❌ | |
| Slash commands | ❌ | |

### WhatsApp Adapter

| Capability | Status | Notes |
|---|---|---|
| Text send/edit | ✅ | Via local Baileys bridge HTTP API |
| Streaming | ✅ | `WhatsAppStreamSink`, 4096 char limit |
| Self-chat mode | ✅ | Only accepts messages from own account |
| Bot mode | ✅ | Accepts all, Python allowlist gates |
| Markdown conversion | ✅ | `markdown_to_whatsapp()` |
| Dedup | ✅ | By message ID |
| Auto-restart bridge | ✅ | After 5 consecutive poll errors |
| LID resolution | ✅ | Resolves opaque LIDs to phone numbers |
| Approval prompt | Text only | Uses base class default |
| File upload | ❌ | |
| Threads | ❌ | |
| Reactions | ❌ | |

### Discord Adapter

| Capability | Status | Notes |
|---|---|---|
| Text send/edit | ✅ | Via `discord.py` gateway |
| Streaming | ✅ | `DiscordStreamSink`, 2000 char limit |
| Approval prompt | ✅ Buttons | `_ApprovalView` with ✅/❌/✔️ buttons, fail-closed timeout |
| Markdown conversion | ✅ | `markdown_to_discord()` |
| Dedup | ✅ | By message ID, 500-entry cap |
| DM auto-respond | ✅ | All DMs accepted |
| @mention required | ✅ | In guild channels, only responds to mentions |
| Message Content intent | ✅ | Required, enabled in connect() |
| File upload | ❌ | |
| Reactions | ❌ | |
| Threads | ❌ | |
| Slash commands | ❌ | |
| Typing indicator | ❌ | |
| Voice | ❌ | |
| Embeds | ❌ | |

---

## 2. Hermes Agent — Discord Capabilities

### Adapter-level

| Capability | Status | Implementation |
|---|---|---|
| Text send | ✅ | `send()` |
| File/image upload | ✅ | `_send_file_attachment`, batch up to 10 attachments |
| Attachment download | ✅ | Authenticated bot session, SSRF-guarded |
| Embeds | ✅ | `discord.Embed` in every interactive prompt |
| Buttons/Views | ✅ | Exec approval (4 buttons), slash confirm, model picker, clarify, choice picker |
| Select menus | ✅ | Provider→model cascade in model picker |
| Slash commands | ✅ | 25+ native (`/new`, `/model`, `/approve`, `/deny`, `/thread`, etc.) + skill group + auto from CLI, 100 cap |
| Threads | ✅ | Auto-thread on @mention, participation tracker, rename, handoff, forum topic inheritance |
| Reactions | ✅ | `add_reaction`/`remove_reaction`, ack→final swap, env toggle |
| Typing indicator | ✅ | `send_typing`, persistent loop per channel |
| Voice (outbound) | ✅ | Join/leave/play TTS/send voice, Opus autoload |
| Voice (inbound) | ✅ | `VoiceReceiver`: RTP/NaCl/DAVE decrypt, Opus decode, silence detection |
| Voice mixer | ✅ | `VoiceMixer`: ambient bed + ducked speech, numpy mix |
| AllowedMentions | ✅ | Deny @everyone/roles by default |
| WebSocket liveness | ✅ | Probe interval, failure threshold, heartbeat-ACK age |
| Text batching | ✅ | `_pending_text_batches` merges rapid messages |
| Non-conversational partitioning | ✅ | Persisted ID set so status bumps don't act as history boundaries |

### Agent-exposed tools (`tools/discord_tool.py`)

| Tool | Actions |
|---|---|
| `discord` (core) | `fetch_messages`, `search_members`, `create_thread` |
| `discord_admin` (server mgmt) | `list_guilds`, `server_info`, `list_channels`, `channel_info`, `list_roles`, `member_info`, `search_members`, `list_pins`, `pin_message`, `unpin_message`, `delete_message`, `add_role`, `remove_role` |

Both toolsets are intent- and config-gated, with per-action 403 enrichment and capability detection disk-cached (24h TTL).

---

## 3. OpenClaw — Discord Capabilities

| Capability | Status | Implementation |
|---|---|---|
| Text send | ✅ | `sendMessageDiscord` via outbound adapter |
| Edit message | ✅ | Outbound finalizer (`finalEdit`, `normalFallback`) |
| Streaming | ✅ | `draftPreview`, `previewFinalization`, `progressUpdates` |
| File/image upload | ✅ | `mediaMaxMb`, multipart, forum-thread starter attachments |
| Components (buttons/selects) | ✅ | Agent-driven via `messageToolHints` (model decides UI) |
| Forms/modals | ✅ | `components.modal` with trigger button + submission routing |
| Slash commands | ✅ | Deploy hash store, sync policies (safe/bulk/off), 100 cap |
| Threads | ✅ | Thread bindings manager, idle timeout, max age, ACP matching |
| Typing indicator | ✅ | `heartbeat.sendTyping` |
| Approvals | ✅ | `approvalCapability`, exec-approval suppression |
| Multi-account | ✅ | First-class: list accounts, default account, startup stagger |
| Permissions audit | ✅ | `fetchChannelPermissionsDiscord`, `auditDiscordChannelPermissions` |
| Directory | ✅ | `listPeers`/`listGroups` (config + live runtime) |
| Security/pairing | ✅ | `discordSecurityAdapter`, DM pairing with prefix stripping |
| Per-channel policy | ✅ | Group policy, allowlist nested guild/channel overrides |
| Mention handling | ✅ | `stripPatterns`, `mentionAliases`, group require-mention |
| Reply-to mode | ✅ | Per-account (off/first/all) |
| Voice | Not in channel.ts | Lives in sibling modules |
| Reactions | Not in channel.ts | Lives in sibling modules |

---

## 4. Side-by-side Comparison (Discord only)

| Capability | Agent8088 | Hermes | OpenClaw |
|---|---|---|---|
| Text send/edit/stream | ✅ | ✅ | ✅ |
| Approval buttons | ✅ 3 buttons | ✅ 4 buttons | ✅ agent-driven |
| File/image upload | ❌ | ✅ batch 10 | ✅ |
| Attachment download | ❌ | ✅ SSRF-guarded | ✅ |
| Embeds | ❌ | ✅ | ✅ indirect |
| Reactions | ❌ | ✅ ack→final swap | ❌ in channel.ts |
| Slash commands | ❌ | ✅ 25+ native | ✅ 100 cap |
| Threads | ❌ | ✅ auto+tracker+forum | ✅ bindings+timeout |
| Typing indicator | ❌ | ✅ | ✅ |
| Voice (outbound) | ❌ | ✅ TTS+play | ❌ in channel.ts |
| Voice (inbound) | ❌ | ✅ RTP/Opus/DAVE | ❌ in channel.ts |
| Voice mixer | ❌ | ✅ ambient+duck | ❌ |
| Select menus | ❌ | ✅ model picker | ✅ agent-driven |
| Forms/modals | ❌ | ❌ | ✅ |
| Multi-account | ❌ | ❌ | ✅ |
| Permissions audit | ❌ | ❌ | ✅ |
| Agent server tools | ❌ | ✅ 15 actions | ✅ message actions |
| AllowedMentions | ❌ | ✅ | ✅ (strip patterns) |
| WebSocket liveness | ❌ | ✅ | ✅ (status adapter) |
| Text batching | ❌ | ✅ | ❌ |
| Forum channels | ❌ | ✅ type-15 | implied |
| Per-channel policy | ❌ | ✅ | ✅ nested overrides |
| Security/pairing | allowlist only | DM pairing | DM pairing + prefix strip |
| Non-conversational partitioning | ❌ | ✅ | ❌ |

---

## 5. Capability Gap Summary

### Agent8088 Discord — what's missing vs both competitors

| Priority | Capability | Effort | Impact |
|---|---|---|---|
| High | File/image upload | Small | Agent can share screenshots, code files, PDFs |
| High | Reactions (ack ✅ on receipt) | Small | User knows agent is working before long responses |
| High | Typing indicator | Small | Better UX — user sees agent is "typing" |
| Medium | Threads | Medium | Long conversations don't clutter channels |
| Medium | Slash commands | Medium | Native Discord UX for `/new`, `/help`, `/mode` |
| Medium | Embeds | Small | Rich formatted responses (fields, colors, links) |
| Low | Select menus | Small | Model picker, choice prompts |
| Low | Agent server tools | Large | `fetch_messages`, `list_channels`, `pin_message` |
| Low | Voice | Large | TTS playback, voice input |
| Low | Multi-account | Large | Multiple bot identities |
| Low | Permissions audit | Medium | Security diagnostics |

### What Agent8088 already matches

- Approval UI (buttons with fail-closed timeout) — matches Hermes pattern, smallest diff
- Text send/edit/stream — all three have this
- Markdown conversion — all three have this
- Dedup — all three have this

---

## 6. References

- Agent8088 `src/agent8088/gateway/platforms/base.py` — BaseChannelAdapter
- Agent8088 `src/agent8088/gateway/platforms/slack.py` — SlackAdapter
- Agent8088 `src/agent8088/gateway/platforms/whatsapp.py` — WhatsAppAdapter
- Agent8088 `src/agent8088/gateway/platforms/discord.py` — DiscordAdapter
- Hermes Agent `plugins/platforms/discord/adapter.py` — DiscordAdapter
- Hermes Agent `tools/discord_tool.py` — agent-exposed Discord tools
- OpenClaw `extensions/discord/src/channel.ts` — discordPlugin
- OpenClaw `extensions/discord/src/approval-handler.runtime.ts` — approval buttons