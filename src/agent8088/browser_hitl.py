"""Human-in-the-loop (HITL) action for browser-use in Agent8088."""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Callable, Optional

from browser_use import Controller

_log = logging.getLogger(__name__)


def format_hitl_prompt(question: str, reason: str = "", headless: bool | None = None) -> str:
    """Format a styled CLI notification box requesting human intervention."""
    import textwrap
    if headless is None:
        try:
            from agent8088 import engine as A
            headless = getattr(A, "_browser_headless", lambda: True)()
        except Exception:
            headless = True

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
    if headless:
        action = "Action:   Type your answer below and press [Enter]:"
        lines.extend([
            f"│ {action:<{width}} │",
            "╰" + ("─" * (width + 2)) + "╯",
        ])
    else:
        action_1 = "Action:   Complete in the browser window and press [Enter],"
        action_2 = "          or type your answer below:"
        lines.extend([
            f"│ {action_1:<{width}} │",
            f"│ {action_2:<{width}} │",
            "╰" + ("─" * (width + 2)) + "╯",
        ])
    return "\n".join(lines)


def _format_empty_human_response() -> str:
    """Return an appropriate message to the agent when the user presses Enter without typing."""
    try:
        from agent8088 import engine as A
        headless = getattr(A, "_browser_headless", lambda: True)()
    except Exception:
        headless = True

    if headless:
        return (
            "Action completed in the browser (running in headless mode). "
            "User pressed [Enter] without providing typed input. Do not ask the same question again; "
            "choose a sensible default option (e.g. the first available choice) and proceed with the task."
        )
    return (
        "Action completed in the browser by user. "
        "Inspect the updated page state to verify. "
        "If a choice or clarification was requested, please proceed using your best judgment."
    )


def format_human_answer_for_agent(ans: str) -> str:
    """Format the human user's typed input into a clear directive for the browser agent."""
    if not ans:
        return _format_empty_human_response()
    if ans.startswith("Action completed in the browser") or ans.startswith("No human input available"):
        return ans
    return (
        f"User answered: {ans}. "
        "Proceed immediately to execute this choice or action on the webpage using the appropriate browser action. "
        "Do not call 'done' until you have actually performed the action on the page."
    )


def _drain_keys() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.FlushConsoleInputBuffer(ctypes.windll.kernel32.GetStdHandle(-10))
        except Exception:
            pass
        try:
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getch()
        except Exception:
            pass


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

    _drain_keys()
    try:
        ans = input("8088 [Human input] › ").strip()
    except (EOFError, KeyboardInterrupt):
        ans = ""

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
        "clarification on which choice to make on a page.",
        terminates_sequence=True,
    )
    async def ask_human(question: str, reason: str = "") -> str:
        # Bring browser window to front if running headfully
        try:
            from agent8088 import engine as A
            if not getattr(A, "_browser_headless", lambda: True)():
                from agent8088.browser_session import bring_browser_to_front
                bring_browser_to_front()
        except Exception as e:
            _log.debug("bring_browser_to_front failed: %s", e)

        # Wait for user input asynchronously in thread pool so event loop isn't blocked
        loop = asyncio.get_running_loop()
        answer = await loop.run_in_executor(None, lambda: request_human_input(question, reason))

        if on_human_input:
            try:
                on_human_input(question, reason, answer)
            except Exception as e:
                _log.debug("on_human_input callback failed: %s", e)

        return format_human_answer_for_agent(answer)

    return controller
