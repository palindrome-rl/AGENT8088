from agent8088 import engine as A
from agent8088.gateway.session import SessionStore


def run_turn(session_key: str, user_text: str, session_store: SessionStore) -> str:
    """Load a JSON session, run the agent loop, save it, return the answer."""
    messages = session_store.load(session_key)
    messages.append({"role": "user", "content": user_text})

    answer = A.run_agent(
        messages,
        max_turns=int(A.APP_CONFIG.get("max_turns", "10")),
        temperature=float(A.APP_CONFIG.get("temperature", "0.1")),
        system_prompt=A.BASE_SYSTEM_PROMPT,
        tools_def=A.build_tools_def(A.TOOL_SPECS),
        allowed_tools=set(A.TOOL_SPECS),
    )

    messages.append({"role": "assistant", "content": answer})
    session_store.save(session_key, messages)
    return answer