"""Where the two branches' behaviours meet.

Neither side is wrong on its own. `development` stops the agent burning a fetch on
something a search already answered. `kazmi_improvements` lets a user approve a
plan and have it run. Put together, a plan-mode turn does exactly the thing the
gate watches for — research with a search, then execute the approved steps in the
same turn — and a step the user just authorised is refused.

Nothing conflicted textually here. `_is_fetch_followup` and the plan-approval
prompt live in different functions, in different files, added by different
branches. This file is the coverage that a clean merge does not give you.
"""
import io

import pytest
from rich.console import Console

from agent8088 import cli

# ---------------------------------------------------------------------------
# An approved plan outranks the post-search fetch gate
# ---------------------------------------------------------------------------

def _approve_a_plan(engine, plan="1. curl the changelog and save it"):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
    engine.run_tool("present_plan", {"plan": plan})


def test_an_approved_plan_step_is_not_an_unsolicited_followup(engine):
    """The user approved these steps. That is the strongest authorisation the
    system has, and it has to outrank a guess about intent — `_user_requested_tool`
    cannot rescue it, because the user approved *a plan*, they never typed
    `execute_shell`."""
    messages = [{"role": "user", "content": "get the changelog for that library"}]
    _approve_a_plan(engine)

    assert engine._is_fetch_followup(
        messages, "execute_shell",
        {"command": "curl https://example.com/CHANGELOG"}) is False


def test_browse_page_in_an_approved_plan_is_allowed(engine):
    messages = [{"role": "user", "content": "check that page"}]
    _approve_a_plan(engine, "1. open the docs page and summarise it")

    assert engine._is_fetch_followup(
        messages, "browse_page", {"url": "https://example.com"}) is False


def test_an_unsolicited_fetch_is_still_blocked_outside_a_plan(engine):
    """The gate must keep doing its job in the ordinary case, or this exemption
    has quietly deleted a feature rather than scoped it."""
    messages = [{"role": "user", "content": "what is the latest version"}]
    engine.PERMISSION_MODE = "full-auto"
    engine.cancel_plan_session()

    assert engine._is_fetch_followup(
        messages, "execute_shell", {"command": "curl https://example.com"}) is True


def test_being_in_plan_mode_is_not_approval(engine):
    """Only an *approved* plan counts. Entering plan mode is the user asking for a
    plan, not agreeing to one."""
    messages = [{"role": "user", "content": "what is the latest version"}]
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()

    assert engine._is_fetch_followup(
        messages, "execute_shell", {"command": "curl https://example.com"}) is True


def test_the_exemption_ends_with_the_plan(engine):
    """`finish_plan_session` closes the window at the end of the turn, so the
    widening lasts exactly as long as the work the user authorised."""
    messages = [{"role": "user", "content": "get the changelog"}]
    _approve_a_plan(engine)
    assert engine._is_fetch_followup(
        messages, "execute_shell", {"command": "curl https://x.dev"}) is False

    engine.finish_plan_session()

    assert engine._is_fetch_followup(
        messages, "execute_shell", {"command": "curl https://x.dev"}) is True


def test_an_explicit_user_request_still_wins_on_its_own(engine):
    """development's original override is untouched — no plan required."""
    messages = [{"role": "user", "content": "run execute_shell to curl that page"}]
    engine.PERMISSION_MODE = "full-auto"
    engine.cancel_plan_session()

    assert engine._is_fetch_followup(
        messages, "execute_shell", {"command": "curl https://example.com"}) is False


# ---------------------------------------------------------------------------
# The plan-approval prompt pauses the ESC listener
# ---------------------------------------------------------------------------

class _FakeEsc:
    """Stands in for the turn's EscListener, recording whether it was paused."""

    def __init__(self):
        self.active = False

    def paused(self):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            self.active = True
            try:
                yield
            finally:
                self.active = False
        return _cm()


@pytest.fixture
def quiet_console(monkeypatch):
    monkeypatch.setattr(cli, "console",
                        Console(file=io.StringIO(), width=120, color_system=None))


def test_the_plan_approval_prompt_pauses_the_esc_listener(monkeypatch, quiet_console):
    """The same bug development fixed for _handle_escalation, on a prompt it never
    saw: the turn's listener is running and swallows the keystroke meant for
    `Approve plan? (a/e/d)`."""
    esc = _FakeEsc()
    seen = {}

    def _input(*_a, **_k):
        # Not `seen.setdefault(...) or "a"` — when the listener *is* paused that
        # returns True and short-circuits, handing the caller a bool to .strip().
        seen["paused"] = esc.active
        return "a"

    monkeypatch.setattr(cli.console, "input", _input)

    approve = cli._make_plan_approval(live=None, esc=esc)

    assert approve("## Goal\nDo it") == "full-auto"
    assert seen["paused"] is True, "the listener must be paused while the prompt is up"
    assert esc.active is False, "and resumed afterwards"


def test_the_approval_prompt_works_without_a_listener(monkeypatch, quiet_console):
    """The direct `/tool` and export paths have no listener running."""
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: "d")

    assert cli._make_plan_approval(live=None, esc=None)("## Goal\nDo it") == ""


def test_do_chat_hands_the_listener_to_the_approval_prompt():
    """Wiring, not behaviour: the callback is built inside do_chat, where the
    listener exists. Passing it is the whole fix, and it is one easy line to
    forget."""
    import inspect
    source = inspect.getsource(cli.do_chat)
    assert "_make_plan_approval(live, esc)" in source, (
        "do_chat must pass the turn's EscListener to the plan-approval prompt")
