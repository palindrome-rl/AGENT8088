"""Gateway per-user rate limiting.

Every turn serializes behind one global turn lock, so a user who floods does not
just spend their own budget — they starve every other user in the queue.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from agent8088.gateway.platforms.base import MessageEvent
from agent8088.gateway.runner import GatewayRunner, _RateLimiter


def test_allows_up_to_the_limit():
    clock = {"t": 0.0}
    rl = _RateLimiter(per_minute=3, now=lambda: clock["t"])
    assert [rl.allow("u1") for _ in range(3)] == [True, True, True]


def test_blocks_past_the_limit():
    clock = {"t": 0.0}
    rl = _RateLimiter(per_minute=3, now=lambda: clock["t"])
    for _ in range(3):
        rl.allow("u1")
    assert rl.allow("u1") is False


def test_window_refills():
    clock = {"t": 0.0}
    rl = _RateLimiter(per_minute=3, now=lambda: clock["t"])
    for _ in range(3):
        rl.allow("u1")
    clock["t"] = 61.0
    assert rl.allow("u1") is True


def test_window_slides_rather_than_resetting():
    """A hit 30s ago still counts against a 60s window."""
    clock = {"t": 0.0}
    rl = _RateLimiter(per_minute=2, now=lambda: clock["t"])
    assert rl.allow("u1") is True
    clock["t"] = 30.0
    assert rl.allow("u1") is True
    clock["t"] = 40.0
    assert rl.allow("u1") is False   # both hits still inside the window
    clock["t"] = 61.0
    assert rl.allow("u1") is True    # the first hit has aged out


def test_users_are_independent():
    clock = {"t": 0.0}
    rl = _RateLimiter(per_minute=1, now=lambda: clock["t"])
    assert rl.allow("u1") is True
    assert rl.allow("u2") is True
    assert rl.allow("u1") is False


def test_zero_disables_the_limit():
    rl = _RateLimiter(per_minute=0, now=lambda: 0.0)
    assert all(rl.allow("u1") for _ in range(1000))


def test_blocked_user_does_not_accumulate_unbounded_state():
    """Rejected hits must not be recorded, or the window would never drain."""
    clock = {"t": 0.0}
    rl = _RateLimiter(per_minute=2, now=lambda: clock["t"])
    for _ in range(50):
        rl.allow("u1")
    assert len(rl._hits["u1"]) == 2


# --- Integration with the runner ---

def _make_runner(per_minute=2):
    sessions = MagicMock()
    sessions.load = MagicMock(return_value=[])
    sessions.save = MagicMock()
    allowlist = MagicMock()
    allowlist.is_allowed = MagicMock(return_value=True)
    runner = GatewayRunner(sessions=sessions, allowlist=allowlist)
    runner._rate_limiter = _RateLimiter(per_minute=per_minute, now=lambda: 0.0)
    adapter = AsyncMock()
    adapter.platform = "slack"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)
    return runner, adapter


def _event(text="hi"):
    return MessageEvent(platform="slack", chat_id="C1", chat_type="channel",
                        user_id="U1", text=text)


def test_runner_replies_and_stops_when_rate_limited():
    runner, adapter = _make_runner(per_minute=1)
    runner._run_turn = AsyncMock()

    asyncio.run(runner.on_message(_event()))
    assert runner._run_turn.await_count == 1

    asyncio.run(runner.on_message(_event()))
    assert runner._run_turn.await_count == 1  # second message never ran
    last = adapter.send_message.call_args.args[1].lower()
    assert "rate limit" in last


def test_rate_limit_applies_to_slash_commands_too():
    """Otherwise /help is a free channel for flooding the gateway."""
    runner, adapter = _make_runner(per_minute=1)
    asyncio.run(runner.on_message(_event("/help")))
    adapter.send_message.reset_mock()
    asyncio.run(runner.on_message(_event("/help")))
    last = adapter.send_message.call_args.args[1].lower()
    assert "rate limit" in last


def test_disallowed_user_is_dropped_before_consuming_quota():
    """An allowlist rejection must not spend the limiter's budget."""
    runner, adapter = _make_runner(per_minute=1)
    runner.allowlist.is_allowed = MagicMock(return_value=False)
    asyncio.run(runner.on_message(_event()))
    assert runner._rate_limiter._hits == {}


def test_default_limit_comes_from_config():
    sessions, allowlist = MagicMock(), MagicMock()
    runner = GatewayRunner(sessions=sessions, allowlist=allowlist)
    assert runner._rate_limiter.per_minute > 0
