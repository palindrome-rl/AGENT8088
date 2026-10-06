import asyncio
import logging
import re
import secrets
import threading
import time

from agent8088 import engine as A
from agent8088.gateway.agent_bridge import run_turn
from agent8088.gateway.auth import Allowlist
from agent8088.gateway.platforms.base import MessageEvent, spawn_task
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
    "/mode": "Show or set the permission mode (readonly/full-auto)",
    "/plan": "Enter plan mode and (optionally) propose a plan for the given task",
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
            self._maybe_prune(current)
            return False
        window.append(current)
        self._hits[user_id] = window
        self._maybe_prune(current)
        return True

    def _maybe_prune(self, current: float) -> None:
        # ponytail: a `*` allowlist grows _hits by one entry per new user forever
        # (audit L3). Prune only past 512 users — O(n) scan, gateway scale.
        if len(self._hits) <= 512:
            return
        self._hits = {u: ts for u, ts in self._hits.items()
                      if any(current - t < 60.0 for t in ts)}


class _PendingApproval:
    """One pending escalation waiting for a chat reply."""
    def __init__(self, chat_id: str, tool_name: str, change_type: str,
                 session_key: str = "", user_id: str = "", platform: str = "",
                 nonce: str = ""):
        self.chat_id = chat_id
        self.tool_name = tool_name
        self.change_type = change_type
        self.session_key = session_key
        self.user_id = user_id
        self.platform = platform
        # Bound into the button view: a stale Discord view live for 300s must
        # not resolve the NEXT approval in the same chat (audit M3).
        self.nonce = nonce
        self.event = threading.Event()
        self.approved = False
        self.session_scope = False  # /approve session → True


class _PendingPlanApproval:
    """One pending plan-mode present_plan() waiting for a chat reply.

    Distinct from _PendingApproval: an escalation resolves to yes/no, a plan
    resolves to which mode to run it in (mirrors cli.py's _make_plan_approval).
    """
    def __init__(self, chat_id: str, user_id: str = "", platform: str = ""):
        self.chat_id = chat_id
        self.user_id = user_id
        self.platform = platform
        self.event = threading.Event()
        self.mode = ""  # "" means still-declined/keep-planning


def _dedup_reply(clean: str) -> str:
    """Collapse model-loop repeats in one reply (audit M4).

    The model (glm-5.2) repeats "I'll create that file for you now." 24x on a
    single line with no newlines. Two passes: repeated substrings, then
    consecutive identical lines — but only outside code fences, where three
    identical log lines are content, not a loop. Consecutive blank lines are
    kept: collapsing them ate intentional spacing.
    """
    fences = []
    def _stash(m):
        fences.append(m.group(0))
        return f"\x00FENCE{len(fences) - 1}\x00"
    work = re.sub(r"```.*?```", _stash, clean, flags=re.DOTALL)
    work = re.sub(r'(.{10,80}?)(?:\1){2,}', r'\1', work)
    deduped = []
    for line in work.split('\n'):
        if line.strip() and deduped and deduped[-1].rstrip() == line.rstrip():
            continue
        deduped.append(line)
    out = '\n'.join(deduped).strip()
    for i, fence in enumerate(fences):
        out = out.replace(f"\x00FENCE{i}\x00", fence)
    return out


class GatewayRunner:
    def __init__(self, sessions: SessionStore, allowlist: Allowlist):
        # Chat users type these, not the CLI's; the model answers from them.
        A.register_frontend_commands(
            {name: (name, description) for name, description in SLASH_COMMANDS.items()})
        self.sessions = sessions
        self.allowlist = allowlist
        self.adapters = []
        # (AdapterSpec, reason) for enabled adapters that could not load.
        self.disabled_adapters = []
        self._active: dict = {}
        self._pending: dict = {}
        self._lock = asyncio.Lock()
        # Global turn lock: the engine uses module-global state (_last_tool_output,
        # client, PERMISSION_MODE), so only one agent turn can run at a time
        # regardless of which chat it's from. Concurrent turns from different
        # chats would corrupt each other's state.
        self._turn_lock = asyncio.Lock()
        # Which session key is holding the turn lock right now. /stop needs it
        # so it interrupts its own chat's turn and never a bystander's (audit M8).
        self._turn_key = ""
        # Flagged by /stop; run_agent polls it and raises AgentInterrupted.
        self._interrupt = threading.Event()
        # Approval routing: (platform, chat_id) → _PendingApproval.
        self._pending_approvals: dict[tuple[str, str], _PendingApproval] = {}
        # Plan-mode approval routing: (platform, chat_id) → _PendingPlanApproval.
        self._pending_plan_approvals: dict[tuple[str, str], _PendingPlanApproval] = {}
        # Session-scoped approvals are bound to the originating session and user.
        self._session_allowlist: set[tuple[str, str, str]] = set()
        # Per-chat permission mode (audit H3): PERMISSION_MODE is a process
        # global in the engine, and one allowlisted person flipping it to
        # full-auto must not silently apply to every other user's turns. The
        # engine global is (re)applied at the start of each serialized turn,
        # under the turn lock — never from a slash handler, which may run
        # concurrently with another chat's in-flight turn.
        self._default_mode = str(
            A.APP_CONFIG.get("gateway_permission_mode", "readonly") or "readonly")
        self._chat_modes: dict[str, str] = {}
        self._plan_keys: set[str] = set()
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
                    spawn_task(self.on_message(follow_up))
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
                spawn_task(self.on_message(next_evt))
        log.info("turn complete for chat %s (%s)", event.chat_id, event.platform)

    async def _run_turn(self, key: str, event: MessageEvent) -> None:
        adapter = next((a for a in self.adapters if a.platform == event.platform), None)

        async def _finalize(answer: str):
            clean = A.strip_tool_json(answer)
            if not clean.strip():
                return
            clean = _dedup_reply(clean)
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
            nonce = secrets.token_hex(8)
            entry = _PendingApproval(event.chat_id, name, change_type, key, event.user_id,
                                     event.platform, nonce)
            self._pending_approvals[approval_key] = entry
            log.info("escalation: sent approval prompt to %s, waiting for /approve or /deny", event.chat_id)

            # Send the prompt to chat (async, from the agent thread)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    adapter.send_approval_prompt(
                        event.chat_id, name, reason, paths, nonce=nonce,
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

        # present_plan() (plan-only mode's exit point) calls A._plan_on_approval
        # synchronously from the agent thread and blocks on its return value —
        # same shape as _on_escalation above, but the result is which mode to
        # run the plan in ("full-auto"/"readonly"), not a yes/no, mirroring
        # cli.py's _make_plan_approval.
        def _plan_on_approval(plan_text: str) -> str:
            approval_key = (event.platform, event.chat_id)
            entry = _PendingPlanApproval(event.chat_id, event.user_id, event.platform)
            self._pending_plan_approvals[approval_key] = entry
            log.info("plan: sent approval prompt to %s, waiting for /approve or /deny", event.chat_id)

            prompt = (f"{plan_text}\n\n"
                      f"Reply /approve to run it (full-auto), "
                      f"/approve readonly to ask before each write, "
                      f"or /deny to keep planning.")
            try:
                future = asyncio.run_coroutine_threadsafe(
                    adapter.send_message(event.chat_id, prompt), loop,
                )
                future.result(timeout=10)
            except Exception as e:
                log.warning("plan approval prompt failed: %s", e)
                self._pending_plan_approvals.pop(approval_key, None)
                return ""

            if not entry.event.wait(timeout=APPROVAL_TIMEOUT):
                log.warning("plan approval timed out for %s", event.chat_id)
                self._pending_plan_approvals.pop(approval_key, None)
                return ""

            self._pending_plan_approvals.pop(approval_key, None)
            return entry.mode

        restored = ""
        try:
            async with self._turn_lock:
                self._interrupt.clear()
                self._turn_key = key
                # Apply this chat's own permission state (audit H3): the engine
                # global is process-wide, so it is only safe to touch here, with
                # the lock held and no other chat's turn in flight.
                mode = self._chat_modes.get(key, self._default_mode)
                if A.PERMISSION_MODE != mode:
                    A.set_permission_mode(mode)
                if key in self._plan_keys:
                    A.enter_plan_mode()
                A._plan_on_approval = _plan_on_approval
                try:
                    answer = await asyncio.to_thread(
                        run_turn, key, event.text, self.sessions,
                        on_escalation=_on_escalation,
                        platform=event.platform, chat_type=event.chat_type,
                        user_id=event.user_id,
                        interrupt_check=self._interrupt.is_set,
                    )
                finally:
                    A._plan_on_approval = None
                    self._turn_key = ""
                    self._interrupt.clear()
                # Mirrors cli.py's _after_turn_plan_state: an approved plan's
                # turn just finished, so the session goes back to the mode it
                # had before /plan. Under the lock — it mutates the same engine
                # globals the next queued turn is about to apply.
                restored = A.finish_plan_session()
                if restored:
                    self._chat_modes[key] = restored
                    self._plan_keys.discard(key)
            await _finalize(answer)
            if restored and adapter:
                try:
                    await adapter.send_message(
                        event.chat_id, f"plan complete — permission mode back to {restored}.")
                except Exception:
                    pass
        except A.AgentInterrupted:
            log.info("turn interrupted for %s", key)
            if adapter:
                try:
                    await adapter.send_message(event.chat_id, "⏹ Stopped.")
                except Exception:
                    pass
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
            self._plan_keys.discard(key)
            self._chat_modes.pop(key, None)
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
            # The in-flight turn holds the global turn lock and never noticed
            # /stop before — the help text promised an interrupt that never
            # happened (audit M8). Flag the active turn; run_agent polls it.
            if self._turn_key == key and not self._interrupt.is_set():
                self._interrupt.set()
                if adapter:
                    await adapter.send_message(event.chat_id, "Stopping the running turn; queued messages cleared.")
                return True
            if adapter:
                await adapter.send_message(event.chat_id, "Queued messages cleared.")
            return True
        if cmd == "/approve":
            plan_entry = self._pending_plan_approvals.get((event.platform, event.chat_id))
            entry = self._pending_approvals.get((event.platform, event.chat_id))
            # A plan approval and an escalation cannot both be pending in the
            # same chat (present_plan blocks the turn before any further tool
            # call can escalate), so checking plan first is unambiguous.
            if plan_entry and not entry:
                if plan_entry.user_id and plan_entry.user_id != event.user_id:
                    if adapter:
                        await adapter.send_message(event.chat_id, "Only the requester may approve this plan.")
                    return True
                parts = event.text.split(None, 1)
                arg = parts[1].strip().lower() if len(parts) > 1 else ""
                # A typo must not silently mean full-auto: reject unknown args
                # instead of treating everything ≠ "readonly" as full-auto (L1).
                if arg and arg not in ("readonly", "full-auto"):
                    if adapter:
                        await adapter.send_message(
                            event.chat_id,
                            f"Unknown option: {arg}\nUsage: /approve [readonly|full-auto]")
                    return True
                plan_entry.mode = arg or "full-auto"
                plan_entry.event.set()
                if adapter:
                    await adapter.send_message(
                        event.chat_id, f"Plan approved — running it in {plan_entry.mode} mode.")
                return True
            log.info("/approve from %s — pending: %s", event.chat_id, bool(entry))
            if not entry:
                if adapter:
                    await adapter.send_message(event.chat_id, "No pending approval.")
                return True
            if entry.user_id and entry.user_id != event.user_id:
                if adapter:
                    await adapter.send_message(event.chat_id, "Only the requester may approve this action.")
                return True
            # Once resolved, the waiter pops the entry within moments — but in
            # that window a second /approve then /deny could flip a verdict the
            # engine already received (audit L11).
            if entry.event.is_set():
                if adapter:
                    await adapter.send_message(event.chat_id, "Already resolved.")
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
            plan_entry = self._pending_plan_approvals.get((event.platform, event.chat_id))
            entry = self._pending_approvals.get((event.platform, event.chat_id))
            if plan_entry and not entry:
                if plan_entry.user_id and plan_entry.user_id != event.user_id:
                    if adapter:
                        await adapter.send_message(event.chat_id, "Only the requester may deny this plan.")
                    return True
                plan_entry.mode = ""
                plan_entry.event.set()
                if adapter:
                    await adapter.send_message(event.chat_id, "Still in plan mode — nothing was written or run.")
                return True
            log.info("/deny from %s — pending: %s", event.chat_id, bool(entry))
            if not entry:
                if adapter:
                    await adapter.send_message(event.chat_id, "No pending approval.")
                return True
            if entry.user_id and entry.user_id != event.user_id:
                if adapter:
                    await adapter.send_message(event.chat_id, "Only the requester may deny this action.")
                return True
            if entry.event.is_set():
                if adapter:
                    await adapter.send_message(event.chat_id, "Already resolved.")
                return True
            entry.approved = False
            entry.event.set()
            if adapter:
                await adapter.send_message(event.chat_id, "Denied.")
            return True
        if cmd == "/mode":
            # plan-only is deliberately not offered here — /plan is the one
            # door into plan mode (mirrors cli.py's /plan vs /mode split).
            valid = ("readonly", "full-auto")
            key = build_session_key(event.platform, event.chat_type, event.chat_id, event.thread_id)
            arg = event.text.split(None, 1)
            arg = arg[1].strip().lower() if len(arg) > 1 else ""
            if arg == "edit":
                arg = "full-auto"
            current = self._chat_modes.get(key, self._default_mode)
            if key in self._plan_keys:
                current = "plan-only"
            if not arg:
                if adapter:
                    await adapter.send_message(
                        event.chat_id,
                        f"Current mode: {current} (this chat)\n"
                        f"Valid modes: {', '.join(valid)}")
                return True
            if arg not in valid:
                if adapter:
                    await adapter.send_message(
                        event.chat_id,
                        f"Unknown mode: {arg}\nValid modes: {', '.join(valid)}")
                return True
            # Per-chat state only (audit H3): the engine global is applied at
            # the start of this chat's next turn, under the turn lock. Slash
            # handlers must never touch engine globals — another chat's turn
            # may be mid-flight under the lock.
            self._chat_modes[key] = arg
            self._plan_keys.discard(key)
            if adapter:
                await adapter.send_message(event.chat_id, f"Permission mode: {arg} (this chat)")
            return True
        if cmd == "/plan":
            # Mirrors cli.py's cmd_plan: enter plan mode, then let on_message's
            # existing "text after the command" follow-up (see below) run the
            # task in the same turn if one was given inline. Per-chat state
            # only (audit H3); _run_turn applies it under the turn lock.
            key = build_session_key(event.platform, event.chat_type, event.chat_id, event.thread_id)
            self._plan_keys.add(key)
            if adapter:
                await adapter.send_message(
                    event.chat_id,
                    "plan mode — reads only. Agent8088 will research, propose a "
                    "plan, and wait for your approval before anything is written or run.")
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

    # A missing dependency used to be a file-only warning: the gateway came up
    # and that platform simply never answered. Now it is also reported through
    # capabilities.GATEWAY (/doctor, /status) and printed at startup.
    import importlib
    from agent8088.gateway import dependencies as gateway_deps
    disabled = []
    for spec in gateway_deps.enabled_adapters(config):
        try:
            module = importlib.import_module(spec.module)
            runner.register_adapter(getattr(module, spec.cls)(config, runner))
        except ImportError as exc:
            why = gateway_deps.disabled_reason(spec, exc)
            log.warning("%s enabled but %s. Run: %s", spec.platform, why,
                        gateway_deps.GATEWAY_FIX if spec.package else "check config.txt")
            disabled.append((spec, why))
    runner.disabled_adapters = gateway_deps.report(config, disabled)
    return runner