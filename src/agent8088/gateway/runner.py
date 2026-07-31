import asyncio
import logging
import re

from agent8088 import engine as A
from agent8088.gateway.agent_bridge import run_turn
from agent8088.gateway.auth import Allowlist
from agent8088.gateway.platforms.base import MessageEvent
from agent8088.gateway.session import SessionStore, build_session_key

log = logging.getLogger("agent8088.gateway")

SLASH_COMMANDS = {
    "/new": "Clear the current session",
    "/stop": "Interrupt the running turn (queued messages cancel)",
    "/help": "Show available commands",
}


class GatewayRunner:
    def __init__(self, sessions: SessionStore, allowlist: Allowlist):
        self.sessions = sessions
        self.allowlist = allowlist
        self.adapters = []
        self._active: dict = {}
        self._pending: dict = {}
        self._lock = asyncio.Lock()
        # Global turn lock: the engine uses module-global state (_last_tool_output,
        # client, PERMISSION_MODE), so only one agent turn can run at a time
        # regardless of which chat it's from. Concurrent turns from different
        # chats would corrupt each other's state.
        self._turn_lock = asyncio.Lock()

    def register_adapter(self, adapter) -> None:
        self.adapters.append(adapter)

    async def on_message(self, event: MessageEvent) -> None:
        if not self.allowlist.is_allowed(event.user_id):
            log.warning("disallowed user dropped: %s", event.user_id)
            return
        if event.text.startswith("/"):
            parts = event.text.split(None, 1)
            cmd = parts[0].lower()
            handled = await self._handle_slash(event, cmd)
            if handled:
                # If there's text after the command (e.g. "/new what is capital"),
                # process the remaining text as a new agent message.
                remaining = parts[1].strip() if len(parts) > 1 else ""
                if remaining:
                    follow_up = MessageEvent(
                        platform=event.platform, chat_id=event.chat_id,
                        chat_type=event.chat_type, user_id=event.user_id,
                        text=remaining, attachments=[], thread_id=event.thread_id,
                        raw=event.raw,
                    )
                    asyncio.create_task(self.on_message(follow_up))
                return
        key = build_session_key(event.platform, event.chat_type, event.chat_id, event.thread_id)
        async with self._lock:
            if key in self._active:
                self._pending.setdefault(key, []).append(event)
                log.info("queued message for busy session %s", key)
                return
            self._active[key] = True
        try:
            await self._run_turn(key, event)
        finally:
            async with self._lock:
                self._active.pop(key, None)
            queued = self._pending.get(key, [])
            if queued:
                next_evt = queued.pop(0)
                if not queued:
                    del self._pending[key]
                asyncio.create_task(self.on_message(next_evt))

    async def _run_turn(self, key: str, event: MessageEvent) -> None:
        adapter = next((a for a in self.adapters if a.platform == event.platform), None)

        async def _finalize(answer: str):
            clean = A.strip_tool_json(answer)
            if not clean.strip():
                return
            # Deduplicate repeated phrases (model loop within a single response).
            # The model (glm-5.2) repeats "I'll create that file for you now."
            # 24x on a single line with no newlines. Collapse to one occurrence.
            clean = re.sub(r'(.{10,80}?)(?:\1){2,}', r'\1', clean)
            # Also deduplicate consecutive identical lines
            lines = clean.split('\n')
            deduped = []
            for line in lines:
                if not deduped or deduped[-1].rstrip() != line.rstrip():
                    deduped.append(line)
            clean = '\n'.join(deduped).strip()
            if not clean:
                return
            try:
                await adapter.send_message(event.chat_id, clean)
            except Exception as e:
                log.warning("send_message failed: %s", e)

        try:
            async with self._turn_lock:
                answer = await asyncio.to_thread(
                    run_turn, key, event.text, self.sessions,
                )
            await _finalize(answer)
        except Exception as e:
            log.error("turn failed for %s: %s", key, e)
            if adapter:
                try:
                    await adapter.send_message(event.chat_id, f"[error: {e}]")
                except Exception:
                    pass

    async def _handle_slash(self, event: MessageEvent, cmd: str) -> bool:
        if cmd not in SLASH_COMMANDS:
            return False
        adapter = next((a for a in self.adapters if a.platform == event.platform), None)
        if cmd == "/new":
            key = build_session_key(event.platform, event.chat_type, event.chat_id, event.thread_id)
            self.sessions.clear(key)
            if adapter:
                await adapter.send_message(event.chat_id, "Session cleared.")
            return True
        if cmd == "/help":
            lines = [f"{c} - {desc}" for c, desc in SLASH_COMMANDS.items()]
            if adapter:
                await adapter.send_message(event.chat_id, "\n".join(lines))
            return True
        if cmd == "/stop":
            key = build_session_key(event.platform, event.chat_type, event.chat_id, event.thread_id)
            self._pending.pop(key, None)
            if adapter:
                await adapter.send_message(event.chat_id, "Queued messages cleared.")
            return True
        return False

    async def run(self) -> None:
        for adapter in self.adapters:
            await adapter.connect()
        log.info("Gateway running with adapters: %s", [a.platform for a in self.adapters])
        await asyncio.Event().wait()


def build_runner() -> GatewayRunner:
    config = A.APP_CONFIG
    sessions = SessionStore()
    allowlist = Allowlist.from_config(config)
    runner = GatewayRunner(sessions=sessions, allowlist=allowlist)

    # Gateway runs in edit mode — messaging users can't approve y/n prompts
    A.PERMISSION_MODE = "edit"

    if config.get("slack_enabled", "0") in ("1", "true", "True"):
        try:
            from agent8088.gateway.platforms.slack import SlackAdapter
            runner.register_adapter(SlackAdapter(config, runner))
        except ImportError:
            log.warning("Slack enabled but slack-bolt not installed. "
                         "Run: uv pip install -e \".[gateway]\"")
    if config.get("whatsapp_enabled", "0") in ("1", "true", "True"):
        try:
            from agent8088.gateway.platforms.whatsapp import WhatsAppAdapter
            runner.register_adapter(WhatsAppAdapter(config, runner))
        except ImportError:
            log.warning("WhatsApp enabled but httpx not installed. "
                         "Run: uv pip install -e \".[gateway]\"")
    return runner