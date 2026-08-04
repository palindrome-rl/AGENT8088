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


def test_discord_adapter_reads_config_dict():
    from agent8088.gateway.platforms.discord import DiscordAdapter
    config = {
        "discord_bot_token": "test-token",
        "discord_allowed_users": "123456789",
    }
    adapter = DiscordAdapter(config, runner=None)
    assert adapter._token == "test-token"


def test_discord_make_stream_sink():
    from agent8088.gateway.platforms.discord import DiscordAdapter, DiscordStreamSink
    config = {"discord_bot_token": "test-token"}
    adapter = DiscordAdapter(config, runner=None)
    sink = adapter.make_stream_sink("123")
    assert isinstance(sink, DiscordStreamSink)
    assert sink.chat_id == "123"


def test_discord_adapter_supports_streaming():
    from agent8088.gateway.platforms.discord import DiscordAdapter
    config = {"discord_bot_token": "test-token"}
    adapter = DiscordAdapter(config, runner=None)
    assert adapter.supports_streaming() is True
    assert adapter.streaming_overflow_limit() == 2000
