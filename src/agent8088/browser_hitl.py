"""Human-in-the-loop (HITL) action for browser-use in Agent8088."""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Callable, Optional

from browser_use import Controller

_log = logging.getLogger(__name__)


def format_hitl_prompt(question: str, reason: str = "") -> str:
    """Format a styled CLI notification box requesting human intervention."""
    import textwrap
    width = 72
    lines = [
        "",
        "╭─ ⚠️  Human in the Loop Required " + ("─" * (width - 29)) + "╮",
    ]
    for line in textwrap.wrap(f"Question: {question}", width=width):
        lines.append(f"│ {line:<{width}} │")
    if reason:
        for line in textwrap.wrap(f"Reason:   {reason}", width=width):
            lines.append(f"│ {line:<{width}} │")
    action_1 = "Action:   Complete in the browser window and press [Enter],"
    action_2 = "          or type your answer below:"
    lines.extend([
        f"│ {action_1:<{width}} │",
        f"│ {action_2:<{width}} │",
        "╰" + ("─" * (width + 2)) + "╯",
    ])
    return "\n".join(lines)


def request_human_input(question: str, reason: str = "") -> str:
    """Prompt the user for input, delegating to the active UI handler if set."""
    from agent8088 import engine as A
    handler = getattr(A, "human_input_handler", None)
    if handler is not None:
        return handler(question, reason)

    prompt_text = format_hitl_prompt(question, reason)
    print(prompt_text, flush=True)

    # Check if running in an interactive terminal
    if not sys.stdin.isatty():
        _log.warning("ask_human called in non-interactive terminal; auto-resuming")
        return "No human input available (non-interactive terminal). Please proceed if possible or report status."

    try:
        ans = input("8088 [Human input] › ").strip()
    except (EOFError, KeyboardInterrupt):
        ans = ""

    if not ans:
        return "Action completed in the browser by user. Please inspect the updated page state and continue."
    return ans


def create_browser_controller(
    on_human_input: Optional[Callable[[str, str, str], None]] = None,
) -> Controller:
    """Create a browser-use Controller equipped with the ask_human action."""
    controller = Controller()

    @controller.action(
        "Pause the browsing task and ask the human user for intervention or information. "
        "Use this when you encounter bot checks, CAPTCHAs, Cloudflare verification, "
        "2FA/OTP prompts, login credentials, payment/checkout confirmation, or need "
        "clarification on which choice to make on a page."
    )
    async def ask_human(question: str, reason: str = "") -> str:
        # Wait for user input asynchronously in thread pool so event loop isn't blocked
        loop = asyncio.get_running_loop()
        answer = await loop.run_in_executor(None, lambda: request_human_input(question, reason))

        if on_human_input:
            try:
                on_human_input(question, reason, answer)
            except Exception as e:
                _log.debug("on_human_input callback failed: %s", e)

        return answer

    return controller
