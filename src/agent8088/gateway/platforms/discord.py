"""agent8088 Discord adapter.

Connects to Discord via discord.py's gateway (outbound WebSocket,
no public URL required). Approval prompts use interactive ✅/❌ buttons.

Requires (set in config.txt):
  discord_enabled        = 0 or 1
  discord_bot_token      = bot token from Discord Developer Portal
  discord_allowed_users  = user IDs, comma-separated, or * for all
"""

import asyncio
import logging
import re
import time
from typing import Optional

import discord

from agent8088 import engine as A
from agent8088.gateway.platforms.base import (
    BaseChannelAdapter, MessageEvent,
)
from agent8088.gateway.runner import APPROVAL_TIMEOUT

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000
MIN_EDIT_INTERVAL = 0.5
DEDUP_MAX = 500


def markdown_to_discord(text: str) -> str:
    fences = []
    def _stash_fence(m):
        fences.append(m.group(0))
        return f"\x00FENCE{len(fences) - 1}\x00"
    text = re.sub(r"```.*?```", _stash_fence, text, flags=re.DOTALL)

    codes = []
    def _stash_code(m):
        codes.append(m.group(0))
        return f"\x00CODE{len(codes) - 1}\x00"
    text = re.sub(r"`[^`]+`", _stash_code, text)

    text = re.sub(r"^#{1,6}\s+(.+)$", r"**\1**", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)

    for i, code in enumerate(codes):
        text = text.replace(f"\x00CODE{i}\x00", code)
    for i, fence in enumerate(fences):
        text = text.replace(f"\x00FENCE{i}\x00", fence)
    return text


class DiscordStreamSink:
    def __init__(self, adapter: "DiscordAdapter", chat_id: str):
        self.adapter = adapter
        self.chat_id = chat_id
        self.buf = ""
        self.msg: Optional[discord.Message] = None
        self.last_edit = 0.0

    def __call__(self, delta: str) -> None:
        self.buf += delta
        now = time.time()
        if now - self.last_edit < MIN_EDIT_INTERVAL:
            return
        self.last_edit = now
        try:
            asyncio.get_event_loop().create_task(self._flush())
        except RuntimeError:
            pass

    async def _flush(self) -> None:
        text = self.buf[:MAX_MESSAGE_LENGTH]
        if self.msg is None:
            self.msg = await self.adapter._send(self.chat_id, text)
        else:
            await self.adapter._edit(self.chat_id, self.msg, text)

    async def finalize(self, full_text: str) -> None:
        chunks = [
            full_text[i:i + MAX_MESSAGE_LENGTH]
            for i in range(0, len(full_text), MAX_MESSAGE_LENGTH)
        ]
        if not chunks:
            chunks = [""]
        if self.msg is None:
            self.msg = await self.adapter._send(self.chat_id, chunks[0])
        else:
            await self.adapter._edit(self.chat_id, self.msg, chunks[0])
        for chunk in chunks[1:]:
            await self.adapter._send(self.chat_id, chunk)

    def fail(self, err: Exception) -> None:
        logger.warning("Discord stream failed for %s: %s", self.chat_id, err)


class DiscordAdapter(BaseChannelAdapter):
    platform = "discord"

    def __init__(self, config: dict, runner):
        self.config = config
        self.runner = runner
        self._token = A.get_secret(config, "discord_bot_token")
        self._client: Optional[discord.Client] = None
        self._running = False
        self._dedup: set = set()

    async def connect(self) -> None:
        if not self._token:
            logger.error("Discord: discord_bot_token required")
            return
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            logger.info("Discord: authenticated as %s#%s",
                         self._client.user.name, self._client.user.discriminator)

        @self._client.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

        try:
            await self._client.start(self._token)
        except discord.LoginFailure:
            logger.error("Discord: invalid bot token")
            self._client = None
            return
        self._running = True
        logger.info("Discord: gateway connected")

    async def disconnect(self) -> None:
        self._running = False
        if self._client:
            await self._client.close()
            self._client = None

    async def _handle_message(self, message: discord.Message) -> None:
        if message.author == self._client.user:
            return
        if message.author.bot:
            return
        mid = str(message.id)
        if mid in self._dedup:
            return
        self._dedup.add(mid)
        if len(self._dedup) > DEDUP_MAX:
            self._dedup = set(list(self._dedup)[-DEDUP_MAX:])

        is_dm = isinstance(message.channel, discord.DMChannel)
        if not is_dm:
            mentioned = self._client.user in message.mentions
            if not mentioned:
                return

        body = message.content or ""
        if not body.strip():
            return

        chat_id = str(message.channel.id)
        chat_type = "private" if is_dm else "channel"
        msg_event = MessageEvent(
            platform="discord", chat_id=chat_id, chat_type=chat_type,
            user_id=str(message.author.id), text=body, attachments=[],
            thread_id=None, raw={"discord": message},
        )
        await self.runner.on_message(msg_event)

    async def _send(self, chat_id: str, text: str, view=None) -> Optional[discord.Message]:
        if not self._client:
            return None
        body = markdown_to_discord(text)
        try:
            channel = self._client.get_channel(int(chat_id))
            if channel is None:
                channel = await self._client.fetch_channel(int(chat_id))
            return await channel.send(body, view=view) if view else await channel.send(body)
        except Exception as e:
            logger.warning("Discord send failed: %s", e)
            return None

    async def _edit(self, chat_id: str, msg: discord.Message, text: str) -> None:
        body = markdown_to_discord(text)
        try:
            await msg.edit(content=body)
        except Exception as e:
            logger.debug("Discord edit failed: %s", e)

    def make_stream_sink(self, chat_id: str) -> DiscordStreamSink:
        return DiscordStreamSink(self, chat_id)

    async def send_message(self, chat_id: str, text: str, **meta) -> str:
        # Discord's hard limit is 2000 chars (regular) / 4000 (boosted server) —
        # chunk so a long reply (a presented plan, /capabilities, big tool
        # output) doesn't get rejected outright with a 400.
        chunks = [text[i:i + MAX_MESSAGE_LENGTH]
                  for i in range(0, len(text), MAX_MESSAGE_LENGTH)]
        if not chunks:
            chunks = [text]
        first_id = "0"
        for chunk in chunks:
            msg = await self._send(chat_id, chunk)
            if msg and first_id == "0":
                first_id = str(msg.id)
        return first_id

    async def edit_message(self, chat_id: str, msg_id: str, text: str) -> None:
        if not self._client:
            return
        try:
            channel = self._client.get_channel(int(chat_id))
            if channel is None:
                channel = await self._client.fetch_channel(int(chat_id))
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(content=markdown_to_discord(text))
        except Exception as e:
            logger.debug("Discord edit_message failed: %s", e)

    async def on_message(self, event: MessageEvent) -> None:
        await self.runner.on_message(event)

    def supports_streaming(self) -> bool:
        return True

    def streaming_overflow_limit(self) -> Optional[int]:
        return MAX_MESSAGE_LENGTH

    async def send_approval_prompt(self, chat_id: str, tool_name: str,
                                     reason: str, paths: str) -> None:
        """Send an approval prompt with ✅/❌ buttons (Discord ui.View)."""
        text = (f"**Approval Required**\n"
                f"Tool: `{tool_name}`\n"
                f"Reason: {reason}\n"
                f"Paths: {paths}")
        view = _ApprovalView(self.runner, chat_id)
        await self._send(chat_id, text + "\n\nClick a button below to approve or deny.", view=view)


class _ApprovalView(discord.ui.View):
    """Discord button view for approve/deny. Resolves the pending approval
    in the GatewayRunner when a button is clicked."""

    def __init__(self, runner, chat_id: str):
        super().__init__(timeout=APPROVAL_TIMEOUT)
        self.runner = runner
        self.chat_id = chat_id

    def _lookup(self):
        # _pending_approvals is keyed by (platform, chat_id), not chat_id alone —
        # a bare .get(chat_id) always returns None, so buttons never resolve.
        return self.runner._pending_approvals.get(("discord", self.chat_id))

    def _check_clicker(self, entry, interaction):
        # Only the user who triggered the escalation may approve/deny it —
        # matches the slash-command check at runner.py /approve. Returns True if allowed.
        if entry.user_id and entry.user_id != str(interaction.user.id):
            asyncio.create_task(interaction.response.send_message(
                "Only the requester may approve this action.", ephemeral=True))
            return False
        return True

    @discord.ui.button(label="Approve", emoji="✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self._lookup()
        if not entry:
            await interaction.response.send_message("No pending approval.", ephemeral=True)
            return
        if not self._check_clicker(entry, interaction):
            return
        entry.approved = True
        entry.event.set()
        self._disable_all(interaction.message)
        await interaction.response.edit_message(view=self, content=interaction.message.content + "\n\n✅ **Approved**")

    @discord.ui.button(label="Approve (session)", emoji="✔️", style=discord.ButtonStyle.primary)
    async def approve_session(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self._lookup()
        if not entry:
            await interaction.response.send_message("No pending approval.", ephemeral=True)
            return
        if not self._check_clicker(entry, interaction):
            return
        entry.approved = True
        entry.session_scope = True
        entry.event.set()
        self._disable_all(interaction.message)
        await interaction.response.edit_message(view=self, content=interaction.message.content + "\n\n✔️ **Approved (session)**")

    @discord.ui.button(label="Deny", emoji="❌", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self._lookup()
        if not entry:
            await interaction.response.send_message("No pending approval.", ephemeral=True)
            return
        if not self._check_clicker(entry, interaction):
            return
        entry.approved = False
        entry.event.set()
        self._disable_all(interaction.message)
        await interaction.response.edit_message(view=self, content=interaction.message.content + "\n\n❌ **Denied**")

    def _disable_all(self, message):
        for child in self.children:
            child.disabled = True

    async def on_timeout(self):
        """Fail-closed: mark as denied when buttons expire."""
        entry = self._lookup()
        if entry and not entry.event.is_set():
            entry.approved = False
            entry.event.set()
