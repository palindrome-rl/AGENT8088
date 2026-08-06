"""Tests for the Email adapter."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def test_email_adapter_imports():
    from agent8088.gateway.platforms.email import EmailAdapter
    assert EmailAdapter.platform == "email"


def test_email_adapter_reads_config(tmp_path, monkeypatch):
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    monkeypatch.setattr(A, "get_secret", lambda c, k, env=None: {
        "email_address": "test@gmail.com",
        "email_password": "app-password",
        "email_smtp_host": "smtp.gmail.com",
        "email_imap_host": "imap.gmail.com",
    }.get(k, ""))
    config = {
        "email_enabled": "1",
        "email_smtp_port": "587",
        "email_imap_port": "993",
    }
    adapter = EmailAdapter(config, runner=None)
    assert adapter._address == "test@gmail.com"
    assert adapter._smtp_host == "smtp.gmail.com"
    assert adapter._imap_host == "imap.gmail.com"
    assert adapter._smtp_port == 587
    assert adapter._imap_port == 993


def test_email_does_not_support_streaming():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    config = {}
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter(config, runner=None)
    assert adapter.supports_streaming() is False
    assert adapter.streaming_overflow_limit() is None


def test_extract_email_address():
    from agent8088.gateway.platforms.email import _extract_email_address
    assert _extract_email_address("John Doe <john@example.com>") == "john@example.com"
    assert _extract_email_address("john@example.com") == "john@example.com"
    assert _extract_email_address("  Jane <jane@test.org>  ") == "jane@test.org"


def test_is_automated_sender():
    from agent8088.gateway.platforms.email import _is_automated_sender
    import email as email_lib
    msg = email_lib.message.Message()
    assert _is_automated_sender("noreply@example.com", msg) is True
    assert _is_automated_sender("mailer-daemon@example.com", msg) is True
    assert _is_automated_sender("user@example.com", msg) is False
    msg["Auto-Submitted"] = "auto-replied"
    assert _is_automated_sender("user@example.com", msg) is True


def test_verify_sender_pass():
    from agent8088.gateway.platforms.email import _verify_sender
    import email as email_lib
    msg = email_lib.message.Message()
    msg["Authentication-Results"] = "example.com; dmarc=pass header.from=user@example.com"
    assert _verify_sender(msg) is True


def test_verify_sender_fail():
    from agent8088.gateway.platforms.email import _verify_sender
    import email as email_lib
    msg = email_lib.message.Message()
    msg["Authentication-Results"] = "example.com; dmarc=fail header.from=user@example.com"
    assert _verify_sender(msg) is False


def test_verify_sender_no_header():
    from agent8088.gateway.platforms.email import _verify_sender
    import email as email_lib
    msg = email_lib.message.Message()
    assert _verify_sender(msg) is False


def test_decode_header_value():
    from agent8088.gateway.platforms.email import _decode_header_value
    assert _decode_header_value("Hello World") == "Hello World"
    assert _decode_header_value("") == ""


def test_extract_text_body_plain():
    from agent8088.gateway.platforms.email import _extract_text_body
    import email as email_lib
    msg = email_lib.message.Message()
    msg.set_payload("Hello, this is a test email.")
    msg.set_type("text/plain")
    assert "Hello, this is a test email" in _extract_text_body(msg)


def test_email_send_message_no_smtp_host():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=None)
    import asyncio
    result = asyncio.run(adapter.send_message("user@example.com", "test"))
    assert result == "0"


def test_email_edit_message_is_noop():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=None)
    import asyncio
    # Should not raise — just silently pass
    asyncio.run(adapter.edit_message("user@example.com", "123", "edited text"))


def test_email_connect_missing_config():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=None)
    import asyncio
    asyncio.run(adapter.connect())
    assert adapter._running is False


def test_email_verify_sender_enabled_default_false():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=None)
    assert adapter._verify_sender_enabled is False


def test_email_verify_sender_enabled_from_config():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({"email_verify_sender": "1"}, runner=None)
    assert adapter._verify_sender_enabled is True


def test_email_process_message_drops_unauthorized_sender():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    import email as email_lib
    import asyncio

    allowlist = MagicMock()
    allowlist.is_allowed = MagicMock(return_value=False)
    runner = MagicMock()
    runner.allowlist = allowlist

    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=runner)

    msg = email_lib.message.Message()
    msg["From"] = "stranger@evil.com"
    msg["Subject"] = "Test"
    msg.set_payload("Hello")
    msg.set_type("text/plain")

    adapter._process_message(msg)
    allowlist.is_allowed.assert_called_once_with("stranger@evil.com", "email")


def test_email_process_message_accepts_authorized_sender():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    import email as email_lib

    allowlist = MagicMock()
    allowlist.is_allowed = MagicMock(return_value=True)
    runner = MagicMock()
    runner.allowlist = allowlist
    runner.on_message = AsyncMock()

    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=runner)

    msg = email_lib.message.Message()
    msg["From"] = "friend@example.com"
    msg["Subject"] = "Hello"
    msg.set_payload("Hi there!")
    msg.set_type("text/plain")

    adapter._process_message(msg)
    allowlist.is_allowed.assert_called_once_with("friend@example.com", "email")