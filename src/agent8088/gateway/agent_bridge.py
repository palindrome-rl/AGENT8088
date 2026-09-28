from agent8088 import engine as A
from agent8088.engine import _strip_special_tokens
from agent8088.gateway.session import SessionStore


def build_system_prompt(platform: str = "", chat_type: str = "") -> str:
    """Build the shared prompt without leaking the local CLI user's profile.

    Deliberately skips render_persona(): USER.md is one local human's profile,
    and the gateway can be serving many different chat users across several
    platforms — folding it in here would leak the primary user's personal
    details into every other sender's conversation. Skill docs carry no such
    per-user data, so they're included.
    """
    return A.compose_system_prompt(
        include_persona=False, channel=platform, chat_type=chat_type,
    )


PLAN_MODE_MIN_TURNS = 25


def _turn_max_turns(mode: str) -> int:
    """Round budget for this turn. A plan-mode turn does three things in one
    turn — research, propose, then execute everything the user approved — so
    it needs more rounds than a normal exchange. Mirrors cli.py's
    _turn_max_turns; a flat cap here truncated large multi-file plans
    mid-write on the gateway (the CLI never had this problem)."""
    configured = int(A.APP_CONFIG.get("max_turns", "10"))
    if mode == "plan-only":
        return max(configured, PLAN_MODE_MIN_TURNS)
    return configured


def run_turn(session_key: str, user_text: str, session_store: SessionStore,
             on_escalation=None, platform: str = "", chat_type: str = "",
             user_id: str = "", interrupt_check=None) -> str:
    """Load a JSON session, run the agent loop, save it, return the answer.

    If on_escalation is provided, it is called as on_escalation(name, result)
    when a tool result starts with the escalation prefix. It must return True
    (approved) or False (denied). The callback is synchronous (runs in the
    agent thread) and should block until the user responds.

    interrupt_check is polled by the engine loop; when it turns True the turn
    unwinds with AgentInterrupted — /stop's path into a running turn (audit M8).
    """
    messages = session_store.load(session_key)
    # Generation of this session at load time. /new mid-turn bumps it, and the
    # saves below then no-op instead of resurrecting a just-cleared session
    # with the in-flight turn's transcript (audit L4).
    generation = session_store.generation(session_key)
    trajectory_state = session_store.load_trajectory(session_key)
    # Inbound platform text is sanitized before it ever reaches the model. A
    # message containing `<|im_start|>system` is tokenized as a real role
    # boundary by self-hosted ChatML/Llama chat templates, so a plain WhatsApp
    # or Slack message could forge a system turn and grant itself a new
    # permission mode. The engine already strips these from fetched pages and
    # MCP responses; gateway text was the one untreated path in.
    #
    # Deliberately NOT _wrap_untrusted: the sender is allowlisted and is the
    # principal here, so demoting their whole message to "data, never
    # instructions" would stop the gateway from doing anything at all. Sanitize
    # the structure, keep the authority.
    #
    # Imported directly rather than reached through `A` so that patching the
    # engine module in a test cannot silently disable the sanitizer.
    messages.append({"role": "user", "content": _strip_special_tokens(user_text)})

    # Per-sender memory namespace (audit M6): memory_scope_by_identity=1 was
    # inert from the gateway because no identity ever arrived, so every
    # platform user shared the CLI owner's namespace. Scope by platform + user.
    memory_identity = f"{platform}:{user_id}" if platform and user_id else None

    answer = A.run_agent(
        messages,
        max_turns=_turn_max_turns(A.PERMISSION_MODE),
        temperature=float(A.APP_CONFIG.get("temperature", "0.1")),
        memory_source_channel=f"gateway:{platform}" if platform else "gateway",
        memory_identity=memory_identity,
        system_prompt=build_system_prompt(platform=platform, chat_type=chat_type),
        tools_def=A.build_tools_def(A.TOOL_SPECS),
        allowed_tools=set(A.TOOL_SPECS),
        on_escalation=on_escalation,
        interrupt_check=interrupt_check,
        trajectory_state=trajectory_state,
        on_trajectory_state=lambda state: session_store.save(
            session_key, messages, state, generation=generation),
    )

    messages.append({"role": "assistant", "content": answer})
    session_store.save(session_key, messages, trajectory_state, generation=generation)
    return answer