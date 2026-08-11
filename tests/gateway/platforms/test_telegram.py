import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


def test_telegram_adapter_imports():
    from agent8088.gateway.platforms.telegram import (
        TelegramAdapter, TelegramStreamSink, markdown_to_telegram,
    )
    assert TelegramAdapter.platform == "telegram"
    assert TelegramStreamSink is not None
    assert callable(markdown_to_telegram)


def test_markdown_to_telegram_bold():
    from agent8088.gateway.platforms.telegram import markdown_to_telegram
    assert markdown_to_telegram("**bold**") == "*bold*"
    assert markdown_to_telegram("__bold__") == "*bold*"


def test_markdown_to_telegram_italic():
    from agent8088.gateway.platforms.telegram import markdown_to_telegram
    assert markdown_to_telegram("*italic*") == "_italic_"


def test_markdown_to_telegram_header():
    from agent8088.gateway.platforms.telegram import markdown_to_telegram
    assert markdown_to_telegram("# Header") == "*Header*"
    assert markdown_to_telegram("### Sub") == "*Sub*"


def test_markdown_to_telegram_link_preserved():
    from agent8088.gateway.platforms.telegram import markdown_to_telegram
    assert markdown_to_telegram("[text](url)") == "[text](url)"


def test_markdown_to_telegram_code_preserved():
    from agent8088.gateway.platforms.telegram import markdown_to_telegram
    assert "```" in markdown_to_telegram("```py\ncode\n```")
    assert "`inline`" in markdown_to_telegram("has `inline` code")


def test_telegram_adapter_reads_config_dict(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    with patch.object(A, "get_secret", return_value="123:abc"):
        adapter = TelegramAdapter({"telegram_bot_token": "123:abc"}, runner=None)
    assert adapter._token == "123:abc"


def test_telegram_adapter_reads_env_fallback(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    captured = {}

    def fake_secret(c, key, env=None):
        captured["env"] = env
        return "env-token"

    with patch.object(A, "get_secret", side_effect=fake_secret):
        adapter = TelegramAdapter({}, runner=None)
    assert adapter._token == "env-token"
    assert captured["env"] == "TELEGRAM_BOT_TOKEN"


def test_telegram_make_stream_sink(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.telegram import (
        TelegramAdapter, TelegramStreamSink,
    )
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=None)
    sink = adapter.make_stream_sink("123")
    assert isinstance(sink, TelegramStreamSink)
    assert sink.chat_id == "123"


def test_telegram_adapter_supports_streaming(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=None)
    assert adapter.supports_streaming() is True
    assert adapter.streaming_overflow_limit() == 4096


def test_telegram_allowlist_merge(tmp_path, monkeypatch):
    from agent8088.gateway.auth import Allowlist
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    config = {
        "telegram_allowed_users": "111,222",
        "slack_allowed_users": "333",
    }
    al = Allowlist.from_config(config)
    assert al.is_allowed("111", "telegram")
    assert al.is_allowed("222", "telegram")
    assert not al.is_allowed("333", "telegram")


def test_telegram_drops_bot_sender(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    runner = MagicMock()
    runner.on_message = AsyncMock()
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=runner)

    user = MagicMock()
    user.is_bot = True
    user.id = 999
    update = MagicMock()
    update.update_id = 1
    update.effective_user = user
    update.message = MagicMock()
    update.message.text = "hi"
    update.effective_chat = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 123

    asyncio.run(adapter._handle_message(update, None))
    runner.on_message.assert_not_awaited()


def test_telegram_group_gate_requires_mention(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    runner = MagicMock()
    runner.on_message = AsyncMock()
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=runner)
    adapter._bot_user_id = 42
    adapter._bot_username = "mybot"

    user = MagicMock()
    user.is_bot = False
    user.id = 777
    update = MagicMock()
    update.update_id = 2
    update.effective_user = user
    update.message = MagicMock()
    update.message.text = "hello there"
    update.message.reply_to_message = None
    update.message.message_thread_id = None
    update.effective_chat = MagicMock()
    update.effective_chat.type = "group"
    update.effective_chat.id = -100

    # No mention → dropped.
    asyncio.run(adapter._handle_message(update, None))
    runner.on_message.assert_not_awaited()

    # With mention → dispatched.
    update.message.text = "@mybot hello there"
    update.update_id = 3
    asyncio.run(adapter._handle_message(update, None))
    runner.on_message.assert_awaited_once()
    sent = runner.on_message.await_args.args[0]
    assert sent.platform == "telegram"
    assert sent.text == "hello there"


def test_telegram_dedup(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    runner = MagicMock()
    runner.on_message = AsyncMock()
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=runner)

    user = MagicMock()
    user.is_bot = False
    user.id = 5
    update = MagicMock()
    update.update_id = 10
    update.effective_user = user
    update.message = MagicMock()
    update.message.text = "dup"
    update.message.reply_to_message = None
    update.message.message_thread_id = None
    update.effective_chat = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 1

    asyncio.run(adapter._handle_message(update, None))
    asyncio.run(adapter._handle_message(update, None))
    runner.on_message.assert_awaited_once()


def test_telegram_slash_command_reaches_runner_in_dm(tmp_path, monkeypatch):
    """Slash commands like /help must reach the runner in DMs, not be
    silently dropped by a COMMAND filter on the MessageHandler."""
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    runner = MagicMock()
    runner.on_message = AsyncMock()
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=runner)

    user = MagicMock()
    user.is_bot = False
    user.id = 42
    update = MagicMock()
    update.update_id = 20
    update.effective_user = user
    update.message = MagicMock()
    update.message.text = "/help"
    update.message.reply_to_message = None
    update.message.message_thread_id = None
    update.effective_chat = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 9

    asyncio.run(adapter._handle_message(update, None))
    runner.on_message.assert_awaited_once()
    sent = runner.on_message.await_args.args[0]
    assert sent.text == "/help"
    assert sent.platform == "telegram"


def test_telegram_send_message_chunks_long_text(tmp_path, monkeypatch):
    """send_message must chunk at MAX_MESSAGE_LENGTH (4096) so long replies
    (/capabilities output, big tool results) don't hit Telegram's 4096-char
    limit and get dropped."""
    from agent8088.gateway.platforms.telegram import TelegramAdapter, MAX_MESSAGE_LENGTH
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=None)

    send_calls = []

    async def fake_send(chat_id, text):
        send_calls.append(len(text))
        return len(send_calls)  # fake message id

    adapter._send = fake_send
    long_text = "x" * (MAX_MESSAGE_LENGTH + 500)
    result = asyncio.run(adapter.send_message("123", long_text))
    assert len(send_calls) == 2
    assert send_calls[0] == MAX_MESSAGE_LENGTH
    assert send_calls[1] == 500
    assert result == "1"  # first chunk's id


def test_telegram_send_message_short_text_single_send(tmp_path, monkeypatch):
    """Short text should still go through send_message without chunking."""
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=None)

    send_calls = []

    async def fake_send(chat_id, text):
        send_calls.append(text)
        return 42

    adapter._send = fake_send
    asyncio.run(adapter.send_message("123", "short reply"))
    assert len(send_calls) == 1


def test_telegram_approval_uses_base_class_plain_text(tmp_path, monkeypatch):
    """Telegram does NOT override send_approval_prompt — it uses the base
    class plain-text /approve and /deny prompt (same as Slack/WhatsApp)."""
    from agent8088.gateway.platforms.telegram import TelegramAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    with patch.object(A, "get_secret", return_value="t"):
        adapter = TelegramAdapter({"telegram_bot_token": "t"}, runner=None)
    # No override — inherits the base class method.
    assert "send_approval_prompt" not in TelegramAdapter.__dict__
    # Asserted on the instance too: the class-dict check alone would still pass
    # if an intermediate class between Telegram and the base overrode it.
    assert adapter.send_approval_prompt.__qualname__.startswith("BaseChannelAdapter")


def test_telegram_message_handler_uses_block_false(tmp_path, monkeypatch):
    """The MessageHandler must use block=False so PTB dispatches updates
    concurrently. Without it, a turn blocked on an approval deadlocks PTB's
    sequential update loop and /approve never gets processed. Regression
    for the approval-deadlock bug."""
    from unittest.mock import MagicMock, AsyncMock
    import asyncio
    from agent8088 import engine as A
    from agent8088.gateway.platforms import telegram as tg_mod

    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")

    captured = {}

    class FakeMessageHandler:
        def __init__(self, filters, callback, block=True):
            captured["block"] = block
            captured["callback"] = callback

    class FakeApp:
        def __init__(self):
            self.bot = MagicMock()
            self.bot.get_me = AsyncMock(return_value=MagicMock(id=1, username="b"))
            self.updater = MagicMock()
            self.updater.start_polling = AsyncMock()
        def add_handler(self, handler):
            pass
        async def initialize(self): pass
        async def start(self): pass
        async def stop(self): pass
        async def shutdown(self): pass

    class FakeBuilder:
        def token(self, t): return self
        def build(self): return FakeApp()

    fake_app_module = MagicMock()
    fake_app_module.builder = lambda: FakeBuilder()

    monkeypatch.setattr(tg_mod, "MessageHandler", FakeMessageHandler)
    monkeypatch.setattr(tg_mod, "Application", fake_app_module)

    with patch.object(A, "get_secret", return_value="t"):
        adapter = tg_mod.TelegramAdapter({"telegram_bot_token": "t"}, runner=None)
    asyncio.run(adapter.connect())
    assert captured.get("block") is False, (
        "MessageHandler must use block=False — without it, PTB processes "
        "updates sequentially and /approve deadlocks while a turn is "
        "blocked on an approval prompt.")


def test_escalation_request_format_handles_windows_paths():
    """The escalation string uses \\x1f (unit separator) as delimiter so
    Windows paths like C:\\Users\\... don't break the parser. Regression
    for the garbled-approval-prompt bug where paths='C' and reason got
    the rest of the path."""
    from agent8088.engine import request_escalation
    win_path = r"C:\Users\Administrator\test.txt"
    result = request_escalation(
        target_mode="edit",
        paths=[win_path],
        change_type="new_file",
        reason="Tool 'write_file' requires write_text access, which is blocked in readonly mode.",
    )
    assert result.startswith("ESCALATION_REQUEST\x1f")
    parts = result.split("\x1f", 4)
    assert len(parts) == 5
    _, target_mode, change_type, paths, reason = parts
    assert target_mode == "edit"
    assert change_type == "new_file"
    assert paths == win_path, f"paths mangled by colon: {paths!r}"
    assert "write_file" in reason
    assert "readonly" in reason


def test_escalation_request_format_handles_colons_in_reason():
    """Reasons can contain colons (e.g. 'blocked in: readonly mode') — the
    \\x1f delimiter must keep them intact."""
    from agent8088.engine import request_escalation
    result = request_escalation(
        target_mode="edit",
        paths=["/tmp/foo"],
        change_type="new_file",
        reason="Blocked in: readonly mode. Time: now.",
    )
    parts = result.split("\x1f", 4)
    assert len(parts) == 5
    _, _, _, paths, reason = parts
    assert paths == "/tmp/foo"
    assert reason == "Blocked in: readonly mode. Time: now."