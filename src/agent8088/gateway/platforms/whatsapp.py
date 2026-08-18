"""Minimal agent8088 WhatsApp adapter (Baileys bridge, text-only v1).

Connects to a Node.js Baileys bridge running locally on 127.0.0.1.
Inbound: polls GET /messages every 1s
Outbound: POST /send (new message) and POST /edit (edit existing)

Requires (set in config.txt):
  whatsapp_enabled      = 0 or 1
  whatsapp_bridge_port  = 3000
  whatsapp_session_dir  = ~/.local/share/agent8088/whatsapp/session
  whatsapp_allowed_users = +923214567891

Pairing (one-time):
  cd src/agent8088/gateway/platforms/whatsapp_bridge
  npm install
  node bridge.js --pair --session <whatsapp_session_dir>
"""

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from agent8088.gateway.platforms.base import (
    BaseChannelAdapter, MessageEvent, SendResult,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0
HEALTH_CHECK_TIMEOUT = 30.0
MAX_MESSAGE_LENGTH = 4096
MIN_EDIT_INTERVAL = 0.5


def markdown_to_whatsapp(text: str) -> str:
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


class WhatsAppStreamSink:
    def __init__(self, adapter: "WhatsAppAdapter", chat_id: str):
        self.adapter = adapter
        self.chat_id = chat_id
        self.buf = ""
        self.msg_id: Optional[str] = None
        self.last_edit = 0.0

    def __call__(self, delta: str) -> None:
        self.buf += delta
        now = time.time()
        if now - self.last_edit < MIN_EDIT_INTERVAL:
            return
        self.last_edit = now
        # Schedule flush on the adapter's event loop (we may be in a worker thread)
        loop = self.adapter._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._flush(), loop)

    async def _flush(self) -> None:
        text = self.buf[:MAX_MESSAGE_LENGTH]
        if self.msg_id is None:
            result = await self.adapter._send(self.chat_id, text)
            if result and result.ok:
                self.msg_id = result.message_id
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
            result = await self.adapter._send(self.chat_id, chunks[0])
            if result and result.ok:
                self.msg_id = result.message_id
        else:
            await self.adapter._edit(self.chat_id, self.msg_id, chunks[0])
        for chunk in chunks[1:]:
            await self.adapter._send(self.chat_id, chunk)

    def fail(self, err: Exception) -> None:
        logger.warning("WhatsApp stream failed for %s: %s", self.chat_id, err)


class WhatsAppAdapter(BaseChannelAdapter):
    platform = "whatsapp"

    def __init__(self, config: dict, runner):
        self.config = config
        self.runner = runner
        self.bridge_port = int(config.get("whatsapp_bridge_port", "3000") or "3000")
        self.session_dir = Path(
            config.get("whatsapp_session_dir", "") or ""
        ).expanduser() or Path.home() / ".local" / "share" / "agent8088" / "whatsapp" / "session"
        self.bridge_url = f"http://127.0.0.1:{self.bridge_port}"
        self.client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._bridge_process: Optional[subprocess.Popen] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self) -> None:
        self._loop = asyncio.get_event_loop()
        self.client = httpx.AsyncClient(timeout=30.0)
        try:
            resp = await self.client.get(f"{self.bridge_url}/health", timeout=5.0)
            if resp.status_code == 200 and resp.json().get("status") == "connected":
                logger.info("WhatsApp: reusing existing bridge at %s", self.bridge_url)
                self._running = True
                self._poll_task = asyncio.create_task(self._poll_messages())
                return
        except Exception:
            pass

        bridge_dir = Path(__file__).parent / "whatsapp_bridge"
        bridge_js = bridge_dir / "bridge.js"
        if not bridge_js.exists():
            logger.error("WhatsApp: bridge.js not found at %s", bridge_js)
            await self.client.aclose()
            self.client = None
            return

        creds = self.session_dir / "creds.json"
        if not creds.exists():
            logger.error(
                "WhatsApp: no session at %s. Run pairing first:\n"
                "  cd %s && npm install\n"
                "  node bridge.js --pair --session %s",
                self.session_dir, bridge_dir, self.session_dir,
            )
            await self.client.aclose()
            self.client = None
            return

        self.session_dir.mkdir(parents=True, exist_ok=True)
        whatsapp_mode = self.config.get("whatsapp_mode", "self-chat") or "self-chat"
        logger.info("WhatsApp: starting bridge on port %d (mode: %s)", self.bridge_port, whatsapp_mode)
        try:
            self._bridge_process = subprocess.Popen(
                ["node", str(bridge_js), "--port", str(self.bridge_port),
                 "--session", str(self.session_dir), "--mode", whatsapp_mode],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(bridge_dir),
            )
        except FileNotFoundError:
            logger.error("WhatsApp: node not found. Install Node.js 18+.")
            await self.client.aclose()
            self.client = None
            return

        deadline = time.time() + HEALTH_CHECK_TIMEOUT
        while time.time() < deadline:
            try:
                resp = await self.client.get(f"{self.bridge_url}/health", timeout=3.0)
                if resp.status_code == 200 and resp.json().get("status") == "connected":
                    logger.info("WhatsApp: connected to bridge")
                    self._running = True
                    self._poll_task = asyncio.create_task(self._poll_messages())
                    return
            except Exception:
                pass
            await asyncio.sleep(1.0)

        logger.error("WhatsApp: bridge did not connect within %ds", int(HEALTH_CHECK_TIMEOUT))
        if self._bridge_process:
            self._bridge_process.terminate()
            self._bridge_process = None
        await self.client.aclose()
        self.client = None

    async def disconnect(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._bridge_process:
            self._bridge_process.terminate()
            try:
                self._bridge_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._bridge_process.kill()
            self._bridge_process = None
        if self.client:
            await self.client.aclose()
            self.client = None

    async def _poll_messages(self) -> None:
        url = f"{self.bridge_url}/messages"
        consecutive_errors = 0
        heartbeat = 0
        while self._running:
            try:
                resp = await self.client.get(url, timeout=30.0)
                if resp.status_code == 200:
                    consecutive_errors = 0
                    msgs = resp.json()
                    if msgs:
                        logger.info("WhatsApp: received %d message(s)", len(msgs))
                        heartbeat = 0
                    else:
                        heartbeat += 1
                        if heartbeat % 60 == 0:
                            logger.info("WhatsApp: alive, no new messages (last %d polls)", heartbeat)
                    for msg_data in msgs:
                        await self._handle_message(msg_data)
                else:
                    consecutive_errors += 1
                    if consecutive_errors > 5:
                        logger.warning("WhatsApp: bridge returned %d (5x), may be dead", resp.status_code)
                        await self._restart_bridge()
                        consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors > 5:
                    logger.warning("WhatsApp: poll failed 5x (%s), restarting bridge", e)
                    await self._restart_bridge()
                    consecutive_errors = 0
                elif self._running:
                    logger.debug("WhatsApp poll error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _restart_bridge(self) -> None:
        """Kill and respawn the Node bridge if it has died."""
        logger.warning("WhatsApp: restarting bridge...")
        # Kill old process
        if self._bridge_process:
            self._bridge_process.terminate()
            try:
                self._bridge_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._bridge_process.kill()
            self._bridge_process = None
        # Spawn fresh
        bridge_dir = Path(__file__).parent / "whatsapp_bridge"
        bridge_js = bridge_dir / "bridge.js"
        whatsapp_mode = self.config.get("whatsapp_mode", "self-chat") or "self-chat"
        try:
            self._bridge_process = subprocess.Popen(
                ["node", str(bridge_js), "--port", str(self.bridge_port),
                 "--session", str(self.session_dir), "--mode", whatsapp_mode],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(bridge_dir),
            )
        except Exception as e:
            logger.error("WhatsApp: failed to restart bridge: %s", e)
            return
        # Wait for it to connect
        deadline = time.time() + HEALTH_CHECK_TIMEOUT
        while time.time() < deadline:
            try:
                resp = await self.client.get(f"{self.bridge_url}/health", timeout=3.0)
                if resp.status_code == 200 and resp.json().get("status") == "connected":
                    logger.info("WhatsApp: bridge restarted successfully")
                    return
            except Exception:
                pass
            await asyncio.sleep(1.0)
        logger.error("WhatsApp: bridge restart failed (did not connect in %ds)", int(HEALTH_CHECK_TIMEOUT))

    async def _handle_message(self, data: dict) -> None:
        chat_id = data.get("chatId") or ""
        body = data.get("body") or ""
        if not chat_id or not body:
            return
        user_id = (
            data.get("resolvedSenderId") or data.get("resolvedChatId")
            or data.get("senderId", "") or chat_id
        )
        user_id = user_id.split("@")[0].split(":")[0]
        event = MessageEvent(
            platform="whatsapp", chat_id=chat_id, chat_type="private",
            user_id=user_id, text=body, attachments=[], raw={"whatsapp": data},
        )
        await self.runner.on_message(event)

    async def _send(self, chat_id: str, text: str) -> Optional[SendResult]:
        if not self.client:
            return None
        body = markdown_to_whatsapp(text)
        payload = {"chatId": chat_id, "message": body}
        try:
            resp = await self.client.post(f"{self.bridge_url}/send", json=payload, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                msg_id = data.get("messageId")
                return SendResult(ok=True, message_id=str(msg_id) if msg_id else None)
            return SendResult(ok=False, error=data.get("error", "unknown"))
        except Exception as e:
            # str(e) is empty for some httpx transport-level errors on Windows
            # (e.g. a reset loopback connection) — include the type so a blank
            # message doesn't hide what actually failed.
            logger.warning("WhatsApp send failed: %s: %s", type(e).__name__, e)
            return SendResult(ok=False, error=str(e))

    async def _edit(self, chat_id: str, msg_id: str, text: str) -> None:
        if not self.client:
            return
        body = markdown_to_whatsapp(text)
        payload = {"chatId": chat_id, "messageId": msg_id, "message": body}
        try:
            await self.client.post(f"{self.bridge_url}/edit", json=payload, timeout=15.0)
        except Exception as e:
            logger.debug("WhatsApp edit failed: %s", e)

    def make_stream_sink(self, chat_id: str) -> WhatsAppStreamSink:
        return WhatsAppStreamSink(self, chat_id)

    async def send_message(self, chat_id: str, text: str, **meta) -> str:
        result = await self._send(chat_id, text)
        return result.message_id or "0" if result else "0"

    async def edit_message(self, chat_id: str, msg_id: str, text: str) -> None:
        await self._edit(chat_id, msg_id, text)

    async def on_message(self, event: MessageEvent) -> None:
        await self.runner.on_message(event)

    def supports_streaming(self) -> bool:
        return True

    def streaming_overflow_limit(self) -> Optional[int]:
        return MAX_MESSAGE_LENGTH