import asyncio
import logging
import re
import threading
import time

from agent8088 import engine as A
from agent8088.gateway.agent_bridge import run_turn
from agent8088.gateway.auth import Allowlist
from agent8088.gateway.platforms.base import MessageEvent
from agent8088.gateway.session import SessionStore, build_session_key

log = logging.getLogger("agent8088.gateway")
logging.getLogger("httpx").setLevel(logging.WARNING)

SLASH_COMMANDS = {
    "/new": "Clear the current session",
    "/stop": "Interrupt the running turn (queued messages cancel)",
    "/help": "Show available commands",
    "/capabilities": "Show tools, MCP servers, skills, limits, and active guardrails",
    "/approve": "Approve a pending action (once/session)",
    "/deny": "Deny a pending action",
}

APPROVAL_TIMEOUT = 300  # seconds, fail-closed


class _RateLimiter:
    """Sliding-window per-user message limit. per_minute=0 disables it.

    The gateway serializes every turn behind a single global lock, so a user who
    floods does not just spend their own budget — they starve every other user
    in the queue. Rejected hits are deliberately NOT recorded, so a user who
    keeps hammering still drains out of the window instead of being locked out
    forever.
    """

    def __init__(self, per_minute: int, now=time.monotonic):
        self.per_minute = int(per_minute or 0)
        self._now = now
        self._hits: dict = {}

    def allow(self, user_id: str) -> bool:
        if self.per_minute <= 0:
            return True
        current = self._now()
        window = [t for t in self._hits.get(user_id, []) if current - t < 60.0]
        if len(window) >= self.per_minute:
            self._hits[user_id] = window
            return False
        window.append(current)
        self._hits[user_id] = window
        return True


class _PendingApproval:
    """One pending escalation waiting for a chat reply."""
    def __init__(self, chat_id: str, tool_name: str, change_type: str,
                 session_key: str = "", user_id: str = "", platform: str = ""):
        self.chat_id = chat_id
        self.tool_name = tool_name
        self.change_type = change_type
        self.session_key = session_key
        self.user_id = user_id
        self.platform = platform
        self.event = threading.Event()
        self.approved = False
        self.session_scope = False  # /approve session → True


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
        # Approval routing: (platform, chat_id) → _PendingApproval.
        self._pending_approvals: dict[tuple[str, str], _PendingApproval] = {}
        # Session-scoped approvals are bound to the originating session and user.
        self._session_allowlist: set[tuple[str, str, str]] = set()
        self._rate_limiter = _RateLimiter(
            int(A.APP_CONFIG.get("gateway_rate_limit_per_min", "20")))

    def register_adapter(self, adapter) -> None:
        self.adapters.append(adapter)

    async def on_message(self, event: MessageEvent) -> None:
        # Scope by platform: an id listed under slack_allowed_users must not
        # grant access on discord or whatsapp.
        if not self.allowlist.is_allowed(event.user_id, platform=event.platform):
            log.warning("disallowed user dropped: %s (%s)", event.user_id, event.platform)
            return
        # Applies to slash commands too — otherwise /help is a free flood channel.
        if not self._rate_limiter.allow(event.user_id):
            log.warning("rate limited: %s (%s)", event.user_id, event.platform)
            A._audit("gateway_rate_limited", tool="gateway", decision="blocked",
                     detail=f"{event.platform}:{event.user_id}")
            adapter = next(
                (a for a in self.adapters if a.platform == event.platform), None)
            if adapter:
                await adapter.send_message(
                    event.chat_id,
                    "Rate limit reached — wait a minute and try again.")
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
        log.info("processing message from %s on %s (chat %s): %.80s",
                 event.user_id, event.platform, event.chat_id, event.text)
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
        log.info("turn complete for chat %s (%s)", event.chat_id, event.platform)

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

        # on_escalation runs in the agent thread (sync). It sends the prompt
        # to chat via the async loop, then blocks on a threading.Event until
        # the user replies /approve or /deny.
        loop = asyncio.get_event_loop()

        def _on_escalation(name: str, result: str) -> bool:
            if not result.startswith("ESCALATION_REQUEST"):
                return False
            parts = result.split("\x1f", 4)
            if len(parts) < 5:
                return False
            _, target_mode, change_type, paths, reason = parts
            log.info("escalation: %s wants %s (chat=%s)", name, change_type, event.chat_id)

            # Session-scoped auto-approve: if this change_type was already
            # approved for the session, grant without prompting.
            approval_scope = (key, event.user_id, change_type)
            if approval_scope in self._session_allowlist:
                log.info("escalation: auto-approved (session scope: %s)", change_type)
                A.grant_escalation(change_type)
                return True

            approval_key = (event.platform, event.chat_id)
            entry = _PendingApproval(event.chat_id, name, change_type, key, event.user_id,
                                     event.platform)
            self._pending_approvals[approval_key] = entry
            log.info("escalation: sent approval prompt to %s, waiting for /approve or /deny", event.chat_id)

            # Send the prompt to chat (async, from the agent thread)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    adapter.send_approval_prompt(
                        event.chat_id, name, reason, paths,
                    ),
                    loop,
                )
                future.result(timeout=10)
            except Exception as e:
                log.warning("send_approval_prompt failed: %s", e)
                self._pending_approvals.pop(approval_key, None)
                return False

            # Block until user replies or timeout
            if not entry.event.wait(timeout=APPROVAL_TIMEOUT):
                log.warning("approval timed out for %s", event.chat_id)
                self._pending_approvals.pop(approval_key, None)
                return False

            self._pending_approvals.pop(approval_key, None)
            if entry.approved:
                A.grant_escalation(change_type)
                if entry.session_scope:
                    self._session_allowlist.add(approval_scope)
                return True
            return False

        try:
            async with self._turn_lock:
                answer = await asyncio.to_thread(
                    run_turn, key, event.text, self.sessions,
                    on_escalation=_on_escalation,
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
            self._session_allowlist = {scope for scope in self._session_allowlist if scope[0] != key}
            if adapter:
                await adapter.send_message(event.chat_id, "Session cleared.")
            return True
        if cmd == "/help":
            lines = [f"{c} - {desc}" for c, desc in SLASH_COMMANDS.items()]
            if adapter:
                await adapter.send_message(event.chat_id, "\n".join(lines))
            return True
        if cmd == "/capabilities":
            if adapter:
                await adapter.send_message(event.chat_id, A.describe_capabilities())
            return True
        if cmd == "/stop":
            key = build_session_key(event.platform, event.chat_type, event.chat_id, event.thread_id)
            self._pending.pop(key, None)
            if adapter:
                await adapter.send_message(event.chat_id, "Queued messages cleared.")
            return True
        if cmd == "/approve":
            entry = self._pending_approvals.get((event.platform, event.chat_id))
            log.info("/approve from %s — pending: %s", event.chat_id, bool(entry))
            if not entry:
                if adapter:
                    await adapter.send_message(event.chat_id, "No pending approval.")
                return True
            if entry.user_id and entry.user_id != event.user_id:
                if adapter:
                    await adapter.send_message(event.chat_id, "Only the requester may approve this action.")
                return True
            parts = event.text.split(None, 1)
            entry.session_scope = len(parts) > 1 and parts[1].strip().lower() == "session"
            entry.approved = True
            entry.event.set()
            if adapter:
                scope = "session" if entry.session_scope else "once"
                await adapter.send_message(event.chat_id, f"Approved ({scope}).")
            return True
        if cmd == "/deny":
            entry = self._pending_approvals.get((event.platform, event.chat_id))
            log.info("/deny from %s — pending: %s", event.chat_id, bool(entry))
            if not entry:
                if adapter:
                    await adapter.send_message(event.chat_id, "No pending approval.")
                return True
            if entry.user_id and entry.user_id != event.user_id:
                if adapter:
                    await adapter.send_message(event.chat_id, "Only the requester may deny this action.")
                return True
            entry.approved = False
            entry.event.set()
            if adapter:
                await adapter.send_message(event.chat_id, "Denied.")
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

    # Gateway runs in readonly mode — writes/shell escalate to chat prompts.
    # Users approve via /approve (once/session) or /deny in the chat.
    # Set gateway_permission_mode=edit in config to disable (full-auto).
    mode = A.APP_CONFIG.get("gateway_permission_mode", "readonly")
    A.PERMISSION_MODE = mode

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
    if config.get("discord_enabled", "0") in ("1", "true", "True"):
        try:
            from agent8088.gateway.platforms.discord import DiscordAdapter
            runner.register_adapter(DiscordAdapter(config, runner))
        except ImportError:
            log.warning("Discord enabled but discord.py not installed. "
                         "Run: uv pip install -e \".[gateway]\"")
    if config.get("email_enabled", "0") in ("1", "true", "True"):
        try:
            from agent8088.gateway.platforms.email import EmailAdapter
            runner.register_adapter(EmailAdapter(config, runner))
        except ImportError:
            log.warning("Email enabled but failed to import. "
                        "Email uses stdlib only — check config.")
    if config.get("telegram_enabled", "0") in ("1", "true", "True"):
        try:
            from agent8088.gateway.platforms.telegram import TelegramAdapter
            runner.register_adapter(TelegramAdapter(config, runner))
        except ImportError:
            log.warning("Telegram enabled but python-telegram-bot not installed. "
                        "Run: uv pip install -e \".[gateway]\"")
    return runner
