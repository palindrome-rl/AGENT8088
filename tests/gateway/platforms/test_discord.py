def test_discord_adapter_imports():
    from agent8088.gateway.platforms.discord import DiscordAdapter, DiscordStreamSink, markdown_to_discord
    assert DiscordAdapter.platform == "discord"
    assert DiscordStreamSink is not None
    assert callable(markdown_to_discord)


def test_markdown_to_discord_bold():
    from agent8088.gateway.platforms.discord import markdown_to_discord
    assert markdown_to_discord("**bold**") == "**bold**"


def test_markdown_to_discord_italic():
    from agent8088.gateway.platforms.discord import markdown_to_discord
    assert markdown_to_discord("*italic*") == "*italic*"


def test_markdown_to_discord_header():
    from agent8088.gateway.platforms.discord import markdown_to_discord
    assert markdown_to_discord("# Header") == "**Header**"
    assert markdown_to_discord("### Sub") == "**Sub**"


def test_markdown_to_discord_link():
    from agent8088.gateway.platforms.discord import markdown_to_discord
    assert markdown_to_discord("[text](url)") == "text (url)"


def test_markdown_to_discord_code_preserved():
    from agent8088.gateway.platforms.discord import markdown_to_discord
    assert "```" in markdown_to_discord("```py\ncode\n```")
    assert "`inline`" in markdown_to_discord("has `inline` code")


def test_discord_adapter_reads_config_dict(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.discord import DiscordAdapter
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    config = {
        "discord_bot_token": "test-token",
        "discord_allowed_users": "123456789",
    }
    adapter = DiscordAdapter(config, runner=None)
    assert adapter._token == "test-token"


def test_discord_make_stream_sink(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.discord import DiscordAdapter, DiscordStreamSink
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    config = {"discord_bot_token": "test-token"}
    adapter = DiscordAdapter(config, runner=None)
    sink = adapter.make_stream_sink("123")
    assert isinstance(sink, DiscordStreamSink)
    assert sink.chat_id == "123"


def test_discord_adapter_supports_streaming(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.discord import DiscordAdapter
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    config = {"discord_bot_token": "test-token"}
    adapter = DiscordAdapter(config, runner=None)
    assert adapter.supports_streaming() is True
    assert adapter.streaming_overflow_limit() == 2000


def test_discord_send_message_chunks_long_text(tmp_path, monkeypatch):
    """Discord rejects any single message over ~2000-4000 chars with a 400.
    A long presented plan (or any big reply) must be split into multiple
    sends instead of handed to _send whole."""
    import asyncio
    from unittest.mock import AsyncMock
    from agent8088.gateway.platforms.discord import DiscordAdapter, MAX_MESSAGE_LENGTH
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    config = {"discord_bot_token": "test-token"}
    adapter = DiscordAdapter(config, runner=None)
    adapter._send = AsyncMock(side_effect=lambda chat_id, text, view=None: type(
        "M", (), {"id": 1})())

    long_text = "x" * (MAX_MESSAGE_LENGTH * 3 + 10)
    asyncio.run(adapter.send_message("123", long_text))

    assert adapter._send.call_count == 4
    for call in adapter._send.call_args_list:
        assert len(call.args[1]) <= MAX_MESSAGE_LENGTH


def test_discord_send_message_short_text_sends_once(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from agent8088.gateway.platforms.discord import DiscordAdapter
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    config = {"discord_bot_token": "test-token"}
    adapter = DiscordAdapter(config, runner=None)
    adapter._send = AsyncMock(side_effect=lambda chat_id, text, view=None: type(
        "M", (), {"id": 42})())

    msg_id = asyncio.run(adapter.send_message("123", "hi"))

    assert adapter._send.call_count == 1
    assert msg_id == "42"


def test_discord_approval_view_lookup_uses_tuple_key(tmp_path, monkeypatch):
    """Discord _ApprovalView must look up pending approvals by
    ("discord", chat_id) tuple -- the runner stores entries keyed by
    tuple, not bare string. Regression for the 'No pending approval'
    bug that broke Discord buttons."""
    import threading
    from unittest.mock import MagicMock
    from agent8088.gateway.platforms.discord import _ApprovalView
    from agent8088.gateway.runner import _PendingApproval

    entry = _PendingApproval(
        chat_id="123", tool_name="write_file", change_type="new_file",
        session_key="k", user_id="5", platform="discord")
    entry.event = threading.Event()
    runner = MagicMock()
    runner._pending_approvals = {("discord", "123"): entry}

    view = _ApprovalView(runner, "123")
    assert view._lookup() is entry
    assert runner._pending_approvals.get("123") is None
