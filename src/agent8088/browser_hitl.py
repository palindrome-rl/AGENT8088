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
    lines = [
        "",
        "╭─ ⚠️  Human in the Loop Required ──────────────────────────────────────────╮",
        f"│ Question: {question[:66]:<66} │",
    ]
    if reason:
        lines.append(f"│ Reason:   {reason[:66]:<66} │")
    lines.extend([
        "│ Action:   Complete in the browser window and press [Enter],              │",
        "│           or type your answer below:                                     │",
        "╰──────────────────────────────────────────────────────────────────────────╯",
    ])
    return "\n".join(lines)


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
        prompt_text = format_hitl_prompt(question, reason)
        print(prompt_text, flush=True)

        # Check if running in an interactive terminal
        if not sys.stdin.isatty():
            _log.warning("ask_human called in non-interactive terminal; auto-resuming")
            return "No human input available (non-interactive terminal). Please proceed if possible or report status."

        # Wait for user input asynchronously in thread pool so event loop isn't blocked
        loop = asyncio.get_running_loop()
        answer = await loop.run_in_executor(None, lambda: input("8088 [Human input] › ").strip())

        if on_human_input:
            try:
                on_human_input(question, reason, answer)
            except Exception as e:
                _log.debug("on_human_input callback failed: %s", e)

        if not answer:
            return "Action completed in the browser by user. Please inspect the updated page state and continue."
        return answer

    return controller
