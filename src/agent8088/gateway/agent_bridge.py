from agent8088 import engine as A
from agent8088.engine import _strip_special_tokens
from agent8088.gateway.session import SessionStore


def build_system_prompt() -> str:
    """Build the gateway system prompt: base + tool docs + permission mode,
    mirroring the CLI's _session_system_prompt() so the model knows what
    tools exist and whether writes need approval."""
    prompt = (A.BASE_SYSTEM_PROMPT + "\n" + A.render_tool_docs(A.TOOL_SPECS)
              + A.render_runtime_context())
    prompt += f"\n\n## Current Permission Mode: {A.PERMISSION_MODE}\n"
    if A.PERMISSION_MODE in ("edit", "full-auto"):
        prompt += ("You are in full-auto mode. All tools are allowed without prompts. "
                   "Catastrophic commands and credential path writes are still blocked.\n")
    else:
        prompt += ("You are in readonly mode. Reads and safe shell commands are allowed. "
                   "Writes and mutations require user approval — you will be told when "
                   "a tool is blocked and whether the user approved it.\n")
    return prompt


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
             on_escalation=None) -> str:
    """Load a JSON session, run the agent loop, save it, return the answer.

    If on_escalation is provided, it is called as on_escalation(name, result)
    when a tool result starts with the escalation prefix. It must return True
    (approved) or False (denied). The callback is synchronous (runs in the
    agent thread) and should block until the user responds.
    """
    messages = session_store.load(session_key)
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

    answer = A.run_agent(
        messages,
        max_turns=_turn_max_turns(A.PERMISSION_MODE),
        temperature=float(A.APP_CONFIG.get("temperature", "0.1")),
        system_prompt=build_system_prompt(),
        tools_def=A.build_tools_def(A.TOOL_SPECS),
        allowed_tools=set(A.TOOL_SPECS),
        on_escalation=on_escalation,
    )

    messages.append({"role": "assistant", "content": answer})
    session_store.save(session_key, messages)
    return answer