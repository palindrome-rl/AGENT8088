"""Discord approval-button regression tests.

The buttons resolve a pending escalation by looking it up in
GatewayRunner._pending_approvals, which is keyed by the tuple
(platform, chat_id). A bare .get(chat_id) always misses, so every click
reported "No pending approval." and the blocked turn ran out its 300s
timeout. That shipped because nothing here exercised _ApprovalView.

These tests drive the real button callbacks against the real key layout,
so a future change to either side of the key contract goes red.
"""
import asyncio

from agent8088.gateway.platforms.discord import _ApprovalView
from agent8088.gateway.runner import _PendingApproval


class _FakeResponse:
    def __init__(self):
        self.messages = []
        self.edits = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append(content)

    async def edit_message(self, view=None, content=None):
        self.edits.append(content)


class _FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class _FakeMessage:
    content = "Approval Required"


class _FakeInteraction:
    def __init__(self, user_id):
        self.user = _FakeUser(user_id)
        self.response = _FakeResponse()
        self.message = _FakeMessage()


class _FakeRunner:
    def __init__(self):
        self._pending_approvals = {}


def _view(runner, chat_id="chan-1"):
    return _ApprovalView(runner, chat_id)


def _click(view, handler_name, interaction):
    """Invoke a button handler the way discord.py does: (self, interaction, button).

    The decorator replaces the instance attribute with a Button, so the raw
    coroutine is reached through the class and paired with its own child item.
    """
    handler = getattr(_ApprovalView, handler_name)
    labels = {"approve": "Approve", "approve_session": "Approve (session)",
              "deny": "Deny"}
    button = next(c for c in view.children if c.label == labels[handler_name])
    return asyncio.run(handler(view, interaction, button))


def _pending(runner, chat_id="chan-1", user_id="42"):
    """Register a pending approval the way GatewayRunner._on_escalation does."""
    entry = _PendingApproval(chat_id, "write_file", "new_file",
                             session_key="k", user_id=user_id, platform="discord")
    runner._pending_approvals[("discord", chat_id)] = entry
    return entry


def test_approve_button_resolves_tuple_keyed_pending_approval():
    """The bug: lookup by bare chat_id missed the ('discord', chat_id) key."""
    runner = _FakeRunner()
    entry = _pending(runner)
    view = _view(runner)
    interaction = _FakeInteraction("42")

    _click(view, "approve", interaction)

    assert entry.event.is_set(), "approve button did not unblock the waiting turn"
    assert entry.approved is True
    assert "No pending approval." not in interaction.response.messages


def test_approve_session_button_sets_session_scope():
    runner = _FakeRunner()
    entry = _pending(runner)
    view = _view(runner)

    _click(view, "approve_session", _FakeInteraction("42"))

    assert entry.event.is_set()
    assert entry.approved is True
    assert entry.session_scope is True


def test_deny_button_resolves_and_denies():
    runner = _FakeRunner()
    entry = _pending(runner)
    view = _view(runner)

    _click(view, "deny", _FakeInteraction("42"))

    assert entry.event.is_set()
    assert entry.approved is False


def test_non_requester_cannot_approve():
    """Matches the /approve slash-command check — only the requester decides."""
    runner = _FakeRunner()
    entry = _pending(runner, user_id="42")
    view = _view(runner)
    interaction = _FakeInteraction("999")  # a different channel member

    _click(view, "approve", interaction)

    assert not entry.event.is_set(), "a non-requester resolved someone else's approval"
    assert entry.approved is False


def test_non_requester_cannot_deny():
    runner = _FakeRunner()
    entry = _pending(runner, user_id="42")
    view = _view(runner)

    _click(view, "deny", _FakeInteraction("999"))

    assert not entry.event.is_set()


def test_button_with_no_pending_approval_reports_it():
    runner = _FakeRunner()
    view = _view(runner)
    interaction = _FakeInteraction("42")

    _click(view, "approve", interaction)

    assert interaction.response.messages == ["No pending approval."]


def test_on_timeout_fails_closed():
    """Expiring buttons must deny, not leave the turn hanging."""
    runner = _FakeRunner()
    entry = _pending(runner)
    view = _view(runner)

    asyncio.run(view.on_timeout())

    assert entry.event.is_set(), "timeout left the pending approval unresolved"
    assert entry.approved is False


def test_pending_approval_key_layout_matches_runner():
    """Guard the contract itself: the runner keys by (platform, chat_id)."""
    import inspect

    from agent8088.gateway import runner as runner_mod
    src = inspect.getsource(runner_mod.GatewayRunner._run_turn)
    assert "approval_key = (event.platform, event.chat_id)" in src, (
        "runner changed its _pending_approvals key layout — update _ApprovalView._lookup"
    )
