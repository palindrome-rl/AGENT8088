"""agent8088 Telegram adapter.

Connects to Telegram via python-telegram-bot (long polling, no public URL
required). Approval prompts use the base class plain-text /approve and /deny
commands (same as Slack and WhatsApp).

Requires (set in config.txt):
  telegram_enabled        = 0 or 1
  telegram_bot_token      = bot token from @BotFather
  telegram_allowed_users  = numeric user IDs, comma-separated, or * for all
"""

import asyncio
import logging
import re
import time
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

from agent8088 import engine as A
from agent8088.gateway.platforms.base import (
    BaseChannelAdapter, MessageEvent,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096
MIN_EDIT_INTERVAL = 0.5
DEDUP_MAX = 500


def markdown_to_telegram(text: str) -> str:
    """Convert common Markdown to Telegram's Markdown subset.

    Telegram Markdown mode (not MarkdownV2) supports *bold*, _italic_,
    `code`, ```pre```, and [text](url). Headers become bold.
    """
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

    # Telegram uses *bold* and _italic_ (single markers).
    # Italic first: the negative lookarounds skip ** so bold stays intact,
    # and bold's *output* is not re-caught by a later italic pass.
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"_\1_", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"*\1*", text)
    # Headers -> bold (Telegram has no native header).
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

    for i, code in enumerate(codes):
        text = text.replace(f"\x00CODE{i}\x00", code)
    for i, fence in enumerate(fences):
        text = text.replace(f"\x00FENCE{i}\x00", fence)
    return text


class TelegramStreamSink:
    def __init__(self, adapter: "TelegramAdapter", chat_id: str):
        self.adapter = adapter
        self.chat_id = chat_id
        self.buf = ""
        self.msg_id: Optional[int] = None
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
        if self.msg_id is None:
            self.msg_id = await self.adapter._send(self.chat_id, text)
        else:
            await self.adapter._edit(self.chat_id, self.msg_id, text)

    async def finalize(self, full_text: str) -> None:
        chunks = [
            full_text[i:i + MAX_MESSAGE_LENGTH]
            for i in range(0, len(full_text), MAX_MESSAGE_LENGTH)
        ]
        if not chunks:
            chunks = [""]
        if self.msg_id is None:
            self.msg_id = await self.adapter._send(self.chat_id, chunks[0])
        else:
            await self.adapter._edit(self.chat_id, self.msg_id, chunks[0])
        for chunk in chunks[1:]:
            await self.adapter._send(self.chat_id, chunk)

    def fail(self, err: Exception) -> None:
        logger.warning("Telegram stream failed for %s: %s", self.chat_id, err)


class TelegramAdapter(BaseChannelAdapter):
    platform = "telegram"

    def __init__(self, config: dict, runner):
        self.config = config
        self.runner = runner
        self._token = A.get_secret(config, "telegram_bot_token", "TELEGRAM_BOT_TOKEN")
        self._app: Optional[Application] = None
        self._bot_user_id: Optional[int] = None
        self._bot_username: Optional[str] = None
        self._running = False
        self._dedup: set = set()

    async def connect(self) -> None:
        if not self._token:
            logger.error("Telegram: telegram_bot_token required")
            return
        try:
            self._app = (
                Application.builder()
                .token(self._token)
                .build()
            )
        except Exception as e:
            logger.error("Telegram: failed to build Application: %s", e)
            self._app = None
            return

        # block=False: PTB processes updates sequentially by default, which
        # deadlocks when an agent turn blocks on an approval — the /approve
        # message would never get processed. block=False lets PTB dispatch
        # each update as its own task so slash commands can arrive while a
        # turn is running. The runner's _turn_lock and _active/_pending
        # queueing keep agent turns serialized; /approve takes the slash
        # path which returns before _run_turn, so no turn-lock contention.
        self._app.add_handler(
            MessageHandler(filters.TEXT, self._handle_message, block=False)
        )

        # Cache bot identity for mention/reply gating in groups.
        try:
            me = await self._app.bot.get_me()
            self._bot_user_id = me.id
            self._bot_username = (me.username or "").lower()
        except Exception as e:
            logger.error("Telegram: getMe failed: %s", e)
            self._app = None
            return

        self._running = True
        logger.info("Telegram: authenticated as @%s (id=%s)",
                    self._bot_username, self._bot_user_id)
        try:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error("Telegram: failed to start polling: %s", e)
            self._running = False
            self._app = None

    async def disconnect(self) -> None:
        self._running = False
        if self._app:
            try:
                if self._app.updater:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.debug("Telegram disconnect: %s", e)
            self._app = None

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        if user.is_bot:
            return
        uid = str(update.update_id)
        if uid in self._dedup:
            return
        self._dedup.add(uid)
        if len(self._dedup) > DEDUP_MAX:
            self._dedup = set(list(self._dedup)[-DEDUP_MAX:])

        chat = update.effective_chat
        if chat is None:
            return
        is_dm = chat.type == "private"

        body = (update.message.text or "").strip()
        if not body:
            return

        # Group gate: require @bot mention or reply to one of our messages.
        if not is_dm:
            mentioned = False
            if self._bot_username and f"@{self._bot_username}" in body.lower():
                mentioned = True
            reply = update.message.reply_to_message
            if reply is not None and reply.from_user is not None:
                if reply.from_user.id == self._bot_user_id:
                    mentioned = True
            if not mentioned:
                return
            # Strip the @mention from the body.
            if self._bot_username:
                body = re.sub(rf"@{re.escape(self._bot_username)}\b\s*",
                              "", body, flags=re.IGNORECASE).strip()

        thread_id = None
        if update.message.message_thread_id:
            thread_id = str(update.message.message_thread_id)
        reply_to = None
        if update.message.reply_to_message:
            reply_to = str(update.message.reply_to_message.message_id)

        msg_event = MessageEvent(
            platform="telegram", chat_id=str(chat.id), chat_type=chat.type,
            user_id=str(user.id), text=body, attachments=[],
            thread_id=thread_id, reply_to_message_id=reply_to,
            raw=update.to_dict() if hasattr(update, "to_dict") else None,
        )
        await self.runner.on_message(msg_event)

    async def _send(self, chat_id: str, text: str) -> Optional[int]:
        if not self._app:
            return None
        body = markdown_to_telegram(text)
        try:
            msg = await self._app.bot.send_message(
                chat_id=int(chat_id), text=body, parse_mode="Markdown")
            return msg.message_id
        except Exception as e:
            # Retry without parse_mode if Markdown parsing failed.
            try:
                msg = await self._app.bot.send_message(chat_id=int(chat_id), text=body)
                return msg.message_id
            except Exception as e2:
                logger.warning("Telegram send failed: %s / %s", e, e2)
                return None

    async def _edit(self, chat_id: str, msg_id: int, text: str) -> None:
        if not self._app:
            return
        body = markdown_to_telegram(text)
        try:
            await self._app.bot.edit_message_text(
                text=body, chat_id=int(chat_id), message_id=msg_id,
                parse_mode="Markdown")
        except Exception:
            try:
                await self._app.bot.edit_message_text(
                    text=body, chat_id=int(chat_id), message_id=msg_id)
            except Exception as e:
                logger.debug("Telegram edit failed: %s", e)

    def make_stream_sink(self, chat_id: str) -> TelegramStreamSink:
        return TelegramStreamSink(self, chat_id)

    async def send_message(self, chat_id: str, text: str, **meta) -> str:
        # Chunk at MAX_MESSAGE_LENGTH so long replies (/capabilities, big
        # outputs) don't hit Telegram's 4096-char limit and get dropped.
        body = markdown_to_telegram(text)
        chunks = [body[i:i + MAX_MESSAGE_LENGTH]
                  for i in range(0, len(body), MAX_MESSAGE_LENGTH)]
        if not chunks:
            chunks = [body]
        first_id = "0"
        for chunk in chunks:
            msg_id = await self._send(chat_id, chunk)
            if msg_id and first_id == "0":
                first_id = str(msg_id)
        return first_id

    async def edit_message(self, chat_id: str, msg_id: str, text: str) -> None:
        try:
            await self._edit(chat_id, int(msg_id), text)
        except (TypeError, ValueError):
            logger.debug("Telegram edit_message: bad msg_id %r", msg_id)

    async def on_message(self, event: MessageEvent) -> None:
        await self.runner.on_message(event)

    def supports_streaming(self) -> bool:
        return True

    def streaming_overflow_limit(self) -> Optional[int]:
        return MAX_MESSAGE_LENGTH