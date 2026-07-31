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
    import importlib
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
        from agent8088.gateway.runner import build_runner
        runner = build_runner()
        slack_adapters = [a for a in runner.adapters if a.platform == "slack"]
        assert len(slack_adapters) == 1
        assert slack_adapters[0].bot_token == "xoxb-test"