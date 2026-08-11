"""Tests for the Email adapter."""
import asyncio
import socket
import smtplib
from unittest.mock import MagicMock, AsyncMock, patch


def _make_adapter(monkeypatch, tmp_path, smtp_port="587", runner=None):
    """Build an EmailAdapter with secrets stubbed and a temp .env."""
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
        "email_smtp_port": smtp_port,
        "email_imap_port": "993",
    }
    return EmailAdapter(config, runner=runner)


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


def test_email_verify_sender_enabled_default_true():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=None)
    assert adapter._verify_sender_enabled is True


def test_email_verify_sender_enabled_from_config():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({"email_verify_sender": "1"}, runner=None)
    assert adapter._verify_sender_enabled is True


def test_email_sender_verification_can_be_disabled_for_a_trusted_relay():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({"email_verify_sender": "0"}, runner=None)
    assert adapter._verify_sender_enabled is False


def test_email_process_message_drops_unauthorized_sender():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    import email as email_lib

    allowlist = MagicMock()
    allowlist.is_allowed = MagicMock(return_value=False)
    runner = MagicMock()
    runner.allowlist = allowlist

    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=runner)

    msg = email_lib.message.Message()
    msg["From"] = "stranger@evil.com"
    msg["Authentication-Results"] = "example.com; dmarc=pass"
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
    msg["Authentication-Results"] = "example.com; dmarc=pass"
    msg["Subject"] = "Hello"
    msg.set_payload("Hi there!")
    msg.set_type("text/plain")

    adapter._process_message(msg)
    allowlist.is_allowed.assert_called_once_with("friend@example.com", "email")
    runner.on_message.assert_not_called()


def test_email_rejects_unverified_sender_before_allowlist():
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    import email as email_lib

    allowlist = MagicMock()
    runner = MagicMock(allowlist=allowlist)
    with patch.object(A, "get_secret", return_value=""):
        adapter = EmailAdapter({}, runner=runner)
    msg = email_lib.message.Message()
    msg["From"] = "spoofed@example.com"
    msg.set_payload("hello")
    msg.set_type("text/plain")

    adapter._process_message(msg)

    allowlist.is_allowed.assert_not_called()


# --------------------------------------------------------------------------- #
# SMTP port handling and 587 → 465 fallback
# --------------------------------------------------------------------------- #

def test_email_send_uses_smtp_ssl_when_port_is_465(monkeypatch, tmp_path):
    """Port 465 must use SMTP_SSL (implicit TLS), never STARTTLS."""
    adapter = _make_adapter(monkeypatch, tmp_path, smtp_port="465")
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            calls.append(("SMTP_SSL" if isinstance(self, _FakeSSL) else "SMTP", host, port))
        def login(self, *a, **k): pass
        def send_message(self, m): pass
        def quit(self): pass
        def starttls(self): calls.append(("starttls",))

    class _FakeSSL(FakeSMTP): pass

    with patch("smtplib.SMTP_SSL", _FakeSSL), patch("smtplib.SMTP", FakeSMTP):
        result = adapter._send_email("to@x.com", "subj", "body", "")
    assert result == "1"
    # SMTP_SSL should have been used; plain SMTP should not
    ssl_calls = [c for c in calls if c[0] == "SMTP_SSL"]
    smtp_calls = [c for c in calls if c[0] == "SMTP"]
    starttls_calls = [c for c in calls if c[0] == "starttls"]
    assert len(ssl_calls) == 1
    assert len(smtp_calls) == 0
    assert len(starttls_calls) == 0


def test_email_send_falls_back_to_465_when_587_times_out(monkeypatch, tmp_path):
    """When port 587 times out, the adapter must retry on 465 (SMTP_SSL)."""
    adapter = _make_adapter(monkeypatch, tmp_path, smtp_port="587")
    attempts = []

    class FakeSSL:
        def __init__(self, host, port, timeout=30):
            attempts.append(("SSL", host, port))
        def login(self, *a, **k): pass
        def send_message(self, m): pass
        def quit(self): pass

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            attempts.append(("SMTP", host, port))
        def starttls(self): pass
        def login(self, *a, **k): pass
        def send_message(self, m): pass
        def quit(self): pass

    def smtp_factory_that_times_out(host, port, timeout=30):
        # Record the attempt before raising, so the test can verify the
        # 587-then-465 ordering.
        attempts.append(("SMTP", host, port))
        raise smtplib.SMTPConnectError(421, b"connect error 10060")

    with patch("smtplib.SMTP", smtp_factory_that_times_out), \
         patch("smtplib.SMTP_SSL", FakeSSL):
        result = adapter._send_email("to@x.com", "subj", "body", "")
    assert result == "1"
    # Should have attempted 587 (SMTP) then 465 (SSL)
    ports = [a[2] for a in attempts]
    assert 587 in ports
    assert 465 in ports


def test_email_send_returns_0_when_both_ports_fail(monkeypatch, tmp_path):
    """If both 587 and 465 fail, the adapter returns '0' (no crash)."""
    adapter = _make_adapter(monkeypatch, tmp_path, smtp_port="587")

    def smtp_fail(host, port, timeout=30):
        raise smtplib.SMTPConnectError(421, b"connect error 10060")

    with patch("smtplib.SMTP", smtp_fail), patch("smtplib.SMTP_SSL", smtp_fail):
        result = adapter._send_email("to@x.com", "subj", "body", "")
    assert result == "0"


def test_email_send_explicit_non_587_port_is_honored(monkeypatch, tmp_path):
    """A port other than 587 (e.g. 2525) is tried as-is, with no 465 fallback."""
    adapter = _make_adapter(monkeypatch, tmp_path, smtp_port="2525")
    attempts = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            attempts.append(port)
        def starttls(self): pass
        def login(self, *a, **k): pass
        def send_message(self, m): pass
        def quit(self): pass

    with patch("smtplib.SMTP", FakeSMTP):
        result = adapter._send_email("to@x.com", "subj", "body", "")
    assert result == "1"
    # Only 2525 should be attempted, not 465
    assert attempts == [2525]


def test_email_send_falls_back_on_timeout_error(monkeypatch, tmp_path):
    """socket.timeout / TimeoutError on 587 also triggers the 465 fallback."""
    adapter = _make_adapter(monkeypatch, tmp_path, smtp_port="587")
    attempts = []

    class FakeSSL:
        def __init__(self, host, port, timeout=30):
            attempts.append(port)
        def login(self, *a, **k): pass
        def send_message(self, m): pass
        def quit(self): pass

    def smtp_timeout(host, port, timeout=30):
        raise TimeoutError("timed out reading banner")

    with patch("smtplib.SMTP", smtp_timeout), patch("smtplib.SMTP_SSL", FakeSSL):
        result = adapter._send_email("to@x.com", "subj", "body", "")
    assert result == "1"
    assert 465 in attempts


def test_email_empty_smtp_port_falls_back_to_587(monkeypatch, tmp_path):
    """An empty email_smtp_port value must not crash int(); it defaults to 587."""
    from agent8088.gateway.platforms.email import EmailAdapter
    from agent8088 import engine as A
    monkeypatch.setattr("agent8088.engine.ENV_FILE_PATH", tmp_path / ".env")
    monkeypatch.setattr(A, "get_secret", lambda c, k, env=None: {
        "email_address": "test@gmail.com",
        "email_password": "app-password",
        "email_smtp_host": "smtp.gmail.com",
        "email_imap_host": "imap.gmail.com",
    }.get(k, ""))
    # Stale empty string from the wizard — must not raise ValueError.
    config = {"email_enabled": "1", "email_smtp_port": "", "email_imap_port": ""}
    adapter = EmailAdapter(config, runner=None)
    assert adapter._smtp_port == 587
    assert adapter._imap_port == 993
