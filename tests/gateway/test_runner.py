import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from agent8088.gateway.platforms.base import MessageEvent
from agent8088.gateway.runner import GatewayRunner


def _make_runner(allowlisted=True):
    sessions = MagicMock()
    sessions.load = MagicMock(return_value=[])
    sessions.save = MagicMock()
    sessions.clear = MagicMock()
    allowlist = MagicMock()
    allowlist.is_allowed = MagicMock(return_value=allowlisted)
    runner = GatewayRunner(sessions=sessions, allowlist=allowlist)
    return runner, sessions


def test_on_message_drops_disallowed_user():
    runner, sessions = _make_runner(allowlisted=False)
    evt = MessageEvent(platform="slack", chat_id="C1", chat_type="channel",
                       user_id="U_evil", text="hi")
    asyncio.run(runner.on_message(evt))


def test_on_message_handles_slash_new():
    runner, sessions = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "slack"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)
    evt = MessageEvent(platform="slack", chat_id="C1", chat_type="channel",
                       user_id="U1", text="/new")
    asyncio.run(runner.on_message(evt))
    sessions.clear.assert_called_once()
    adapter.send_message.assert_called_once()


def test_on_message_handles_slash_help():
    runner, sessions = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "slack"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)
    evt = MessageEvent(platform="slack", chat_id="C1", chat_type="channel",
                       user_id="U1", text="/help")
    asyncio.run(runner.on_message(evt))
    adapter.send_message.assert_called_once()
    sent = adapter.send_message.call_args.args[1]
    assert "/new" in sent
    assert "/help" in sent
    assert "/stop" in sent


def test_build_runner_no_adapters_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "nonexistent.txt"))
    with patch("agent8088.gateway.runner.A") as mock_A:
        mock_A.APP_CONFIG = {}
        mock_A.PERMISSION_MODE = "readonly"
        mock_A.BASE_SYSTEM_PROMPT = "sys"
        mock_A.TOOL_SPECS = {}
        mock_A.build_tools_def.return_value = []
        from agent8088.gateway.runner import build_runner
        runner = build_runner()
        assert len(runner.adapters) == 0


def test_build_runner_registers_slack_when_enabled(tmp_path, monkeypatch):
    import sys

    # Create a stub slack module if the real one isn't installed yet
    if "agent8088.gateway.platforms.slack" not in sys.modules:
        stub = type(sys)("agent8088.gateway.platforms.slack")
        class _StubAdapter:
            platform = "slack"
            def __init__(self, config, runner):
                self.bot_token = config.get("slack_bot_token", "")
                self.app_token = config.get("slack_app_token", "")
        stub.SlackAdapter = _StubAdapter
        stub.A = None
        sys.modules["agent8088.gateway.platforms.slack"] = stub

    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "nonexistent.txt"))
    with patch("agent8088.gateway.runner.A") as mock_A:
        mock_A.APP_CONFIG = {
            "slack_enabled": "1",
            "slack_bot_token": "xoxb-test",
            "slack_app_token": "xapp-test",
            "slack_allowed_users": "U01ABC2DEF3",
        }
        mock_A.PERMISSION_MODE = "readonly"
        mock_A.BASE_SYSTEM_PROMPT = "sys"
        mock_A.TOOL_SPECS = {}
        mock_A.build_tools_def.return_value = []
        mock_A.ENV_FILE_PATH = tmp_path / ".env"
        mock_A.get_secret = lambda c, k: c.get(k, "")
        with patch("agent8088.gateway.platforms.slack.A", mock_A):
            from agent8088.gateway.runner import build_runner
            runner = build_runner()
        slack_adapters = [a for a in runner.adapters if a.platform == "slack"]
        assert len(slack_adapters) == 1
        assert slack_adapters[0].bot_token == "xoxb-test"


def test_approve_slash_command_resolves_pending():
    runner, sessions = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "discord"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)

    # Simulate a pending approval
    from agent8088.gateway.runner import _PendingApproval
    entry = _PendingApproval(chat_id="C1", tool_name="write_file", change_type="new_file")
    runner._pending_approvals[("discord", "C1")] = entry

    evt = MessageEvent(platform="discord", chat_id="C1", chat_type="channel",
                       user_id="U1", text="/approve")
    asyncio.run(runner.on_message(evt))
    assert entry.approved is True
    assert entry.event.is_set()


def test_approve_session_sets_session_scope():
    runner, sessions = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "discord"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)

    from agent8088.gateway.runner import _PendingApproval
    entry = _PendingApproval(chat_id="C1", tool_name="write_file", change_type="new_file")
    runner._pending_approvals[("discord", "C1")] = entry

    evt = MessageEvent(platform="discord", chat_id="C1", chat_type="channel",
                       user_id="U1", text="/approve session")
    asyncio.run(runner.on_message(evt))
    assert entry.session_scope is True
    assert entry.approved is True


def test_deny_slash_command_resolves_pending():
    runner, sessions = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "discord"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)

    from agent8088.gateway.runner import _PendingApproval
    entry = _PendingApproval(chat_id="C1", tool_name="write_file", change_type="new_file")
    runner._pending_approvals[("discord", "C1")] = entry

    evt = MessageEvent(platform="discord", chat_id="C1", chat_type="channel",
                       user_id="U1", text="/deny")
    asyncio.run(runner.on_message(evt))
    assert entry.approved is False
    assert entry.event.is_set()


def test_approve_with_no_pending_says_so():
    runner, sessions = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "discord"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)

    evt = MessageEvent(platform="discord", chat_id="C1", chat_type="channel",
                       user_id="U1", text="/approve")
    asyncio.run(runner.on_message(evt))
    sent = adapter.send_message.call_args.args[1]
    assert "No pending" in sent


def test_help_lists_approve_and_deny():
    runner, sessions = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "discord"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)

    evt = MessageEvent(platform="discord", chat_id="C1", chat_type="channel",
                       user_id="U1", text="/help")
    asyncio.run(runner.on_message(evt))
    sent = adapter.send_message.call_args.args[1]
    assert "/approve" in sent
    assert "/deny" in sent


def test_session_allowlist_is_scoped_to_session_user_and_change_type():
    runner, sessions = _make_runner()
    runner._session_allowlist.add(("agent:main:discord:channel:C1", "U1", "new_file"))

    assert ("agent:main:discord:channel:C1", "U1", "new_file") in runner._session_allowlist
    assert ("agent:main:discord:channel:C2", "U1", "new_file") not in runner._session_allowlist
    assert ("agent:main:discord:channel:C1", "U2", "new_file") not in runner._session_allowlist


def test_approval_requires_the_requesting_user():
    runner, _ = _make_runner()
    adapter = AsyncMock()
    adapter.platform = "discord"
    adapter.send_message = AsyncMock(return_value="0")
    runner.register_adapter(adapter)
    from agent8088.gateway.runner import _PendingApproval
    entry = _PendingApproval("C1", "write_file", "new_file", "session", "U1", "discord")
    runner._pending_approvals[("discord", "C1")] = entry

    event = MessageEvent("discord", "C1", "channel", "U2", "/approve")
    asyncio.run(runner.on_message(event))

    assert not entry.event.is_set()
    assert entry.approved is False
    assert "Only the requester" in adapter.send_message.call_args.args[1]
