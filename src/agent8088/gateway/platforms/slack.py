"""agent8088 Slack adapter (Socket Mode, text-only v1).

Connects to Slack via slack-bolt's AsyncSocketModeHandler (outbound
WebSocket, no public URL required).

Requires (set in config.txt):
  slack_enabled        = 0 or 1
  slack_bot_token      = xoxb-...
  slack_app_token      = xapp-... (scope: connections:write)
  slack_allowed_users  = U01ABC2DEF3,U02GHI4JKL
"""

import asyncio
import logging
import re
from typing import Optional

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from agent8088 import engine as A
from agent8088.gateway.platforms.base import (
    BaseChannelAdapter, MessageEvent, SendResult,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 39000
DEDUP_MAX = 500


def markdown_to_slack(text: str) -> str:
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

    text = re.sub(r"(?<!\*)\*(?!\*| )(.+?)(?<! )\*(?!\*)", r"_\1_", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"~\1~", text, flags=re.DOTALL)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)

    for i, code in enumerate(codes):
        text = text.replace(f"\x00CODE{i}\x00", code)
    for i, fence in enumerate(fences):
        text = text.replace(f"\x00FENCE{i}\x00", fence)
    return text


class SlackAdapter(BaseChannelAdapter):
    platform = "slack"

    def __init__(self, config: dict, runner):
        self.config = config
        self.runner = runner
        self.bot_token = A.get_secret(config, "slack_bot_token")
        self.app_token = A.get_secret(config, "slack_app_token")
        self.client: Optional[AsyncWebClient] = None
        self.app: Optional[AsyncApp] = None
        self.handler: Optional[AsyncSocketModeHandler] = None
        self.bot_user_id: str = ""
        self._running = False
        self._socket_task: Optional[asyncio.Task] = None
        # Insertion-ordered dict, not a set: set eviction dropped arbitrary ids
        # under load, which could re-process a duplicate turn (audit M7).
        self._dedup: dict = {}

    async def connect(self) -> None:
        if not self.bot_token or not self.app_token:
            logger.error("Slack: slack_bot_token and slack_app_token required")
            return
        self.client = AsyncWebClient(token=self.bot_token)
        try:
            auth = await self.client.auth_test()
            self.bot_user_id = auth.get("user_id", "")
            logger.info("Slack: authenticated as @%s", auth.get("user", "unknown"))
        except Exception as e:
            logger.error("Slack: auth.test failed: %s", e)
            await self.client.close()
            self.client = None
            return
        self.app = AsyncApp(token=self.bot_token)

        @self.app.event("message")
        async def handle_message(event, say, body):
            await self._handle_slack_message(event)

        @self.app.event("app_mention")
        async def handle_app_mention(event, say, body):
            await self._handle_slack_message(event)

        self.handler = AsyncSocketModeHandler(self.app, self.app_token)
        self._socket_task = asyncio.create_task(self.handler.start_async())
        self._running = True
        logger.info("Slack: Socket Mode connected")

    async def disconnect(self) -> None:
        self._running = False
        if self._socket_task and not self._socket_task.done():
            self._socket_task.cancel()
            try:
                await self._socket_task
            except asyncio.CancelledError:
                pass
        if self.handler:
            try:
                await self.handler.close_async()
            except Exception:
                pass
            self.handler = None
        if self.client:
            await self.client.close()
            self.client = None
        self.app = None

    async def _handle_slack_message(self, event: dict) -> None:
        ts = event.get("ts", "")
        if ts and ts in self._dedup:
            return
        if ts:
            self._dedup[ts] = None
            while len(self._dedup) > DEDUP_MAX:
                self._dedup.pop(next(iter(self._dedup)))
        user_id = event.get("user", "")
        if user_id == self.bot_user_id:
            return
        if event.get("bot_id"):
            return
        body = event.get("text", "")
        if not body:
            return
        channel_type = event.get("channel_type", "")
        if channel_type != "im" and self.bot_user_id not in body:
            return
        body = re.sub(r"<@" + re.escape(self.bot_user_id) + r">", "", body).strip()
        if not body:
            return
        chat_id = event.get("channel", "")
        if not chat_id:
            return
        channel_type = event.get("channel_type", "")
        chat_type = "private" if channel_type == "im" else "channel"
        thread_ts = event.get("thread_ts")
        msg_event = MessageEvent(
            platform="slack", chat_id=chat_id, chat_type=chat_type,
            user_id=user_id, text=body, attachments=[],
            thread_id=thread_ts, raw={"slack": event},
        )
        await self.runner.on_message(msg_event)

    async def _send(self, chat_id: str, text: str, thread_ts: Optional[str] = None) -> Optional[SendResult]:
        if not self.client:
            return None
        body = markdown_to_slack(text)
        try:
            kwargs = {"channel": chat_id, "text": body}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            resp = await self.client.chat_postMessage(**kwargs)
            ts = resp.get("ts") if isinstance(resp, dict) else getattr(resp, "get", lambda k, d=None: d)("ts", None)
            return SendResult(ok=True, message_id=str(ts) if ts else None)
        except Exception as e:
            logger.warning("Slack send failed: %s", e)
            return SendResult(ok=False, error=str(e))

    async def _edit(self, chat_id: str, msg_ts: str, text: str) -> None:
        if not self.client:
            return
        body = markdown_to_slack(text)
        try:
            await self.client.chat_update(channel=chat_id, ts=msg_ts, text=body)
        except Exception as e:
            logger.debug("Slack edit failed: %s", e)

    async def send_message(self, chat_id: str, text: str, **meta) -> str:
        # Chunk like every other adapter: MAX_MESSAGE_LENGTH exists but only the
        # (removed) stream sink used it, so a >=40k reply was rejected outright
        # and the user got nothing (audit M9).
        thread_ts = meta.get("thread_ts") or meta.get("thread_id")
        chunks = [text[i:i + MAX_MESSAGE_LENGTH]
                  for i in range(0, len(text), MAX_MESSAGE_LENGTH)]
        if not chunks:
            chunks = [text]
        first_id = "0"
        for chunk in chunks:
            result = await self._send(chat_id, chunk, thread_ts)
            if result and result.ok and first_id == "0":
                first_id = result.message_id or "0"
        return first_id

    async def edit_message(self, chat_id: str, msg_id: str, text: str) -> None:
        await self._edit(chat_id, msg_id, text)

    async def on_message(self, event: MessageEvent) -> None:
        await self.runner.on_message(event)