"""agent8088 Email adapter (IMAP + SMTP, stdlib only).

Inbound: polls IMAP for unread messages every 15 seconds.
Outbound: sends SMTP replies with In-Reply-To/References headers for
email-native threading.

Uses only Python stdlib (imaplib, smtplib, email) — zero new dependencies.

Requires (set in .env):
  EMAIL_ADDRESS      = user@gmail.com
  EMAIL_PASSWORD     = app-specific password
  EMAIL_SMTP_HOST    = smtp.gmail.com
  EMAIL_IMAP_HOST    = imap.gmail.com

Requires (set in config.txt):
  email_enabled       = 0 or 1
  email_allowed_users = user@example.com,friend@example.com
"""

import asyncio
import email as email_lib
import logging
import re
import smtplib
import socket
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from agent8088 import engine as A
from agent8088.gateway.platforms.base import (
    BaseChannelAdapter, MessageEvent,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 15.0
SEEN_UID_CAP = 2000
MAX_MESSAGE_LENGTH = 50000

# Patterns that indicate automated senders (skip these)
_AUTOMATED_PATTERNS = (
    "noreply", "no-reply", "mailer-daemon", "postmaster", "bounce",
    "notifications@", "notification@", "donotreply", "do-not-reply",
)
_AUTOMATED_HEADERS = ("Auto-Submitted", "Precedence", "X-Auto-Response-Suppress", "List-Unsubscribe")


def _extract_email_address(from_header: str) -> str:
    """Extract the email address from a From header like 'Name <addr@x.com>'.

    A '>' sitting before the '<' must not crash the batch: index(">", start)
    raised ValueError on such headers and aborted the whole uid loop (M11).
    """
    start = from_header.find("<")
    if start != -1:
        end = from_header.find(">", start + 1)
        if end != -1:
            return from_header[start + 1:end].strip().lower()
    return from_header.strip().lower()


def _decode_header_value(value: str) -> str:
    """Decode an email header that may contain encoded words."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _is_automated_sender(from_addr: str, msg: email_lib.message.Message) -> bool:
    """Check if the sender is an automated system (noreply, mailer-daemon, etc.)."""
    addr = from_addr.lower()
    if any(pattern in addr for pattern in _AUTOMATED_PATTERNS):
        return True
    for header in _AUTOMATED_HEADERS:
        if msg.get(header):
            return True
    return False


def _domain_of(address: str) -> str:
    """The domain part of an address or a bare domain, lowercased."""
    return (address or "").strip().strip("<>").rsplit("@", 1)[-1].strip().lower().rstrip(".")


def _domains_aligned(candidate: str, from_domain: str) -> bool:
    """DMARC relaxed alignment: equal, or a subdomain of the From domain.

    Anchored on the dot, so notallowlisted.example is not treated as a
    subdomain of allowlisted.example the way a plain endswith() would.
    """
    if not candidate or not from_domain:
        return False
    return candidate == from_domain or candidate.endswith("." + from_domain)


def _verify_sender(msg: email_lib.message.Message) -> bool:
    """Authenticate the From header. Fail-closed if there is no result header.

    The From header is what authorizes the message a moment later
    (Allowlist.is_allowed), so it is the From header that has to be
    authenticated -- not merely some domain in the message.

    A bare `spf=pass` or `dkim=pass` does not do that, and accepting one was an
    impersonation hole: SPF authenticates the SMTP envelope sender
    (smtp.mailfrom) and DKIM authenticates the signing domain (header.d), and
    neither has to match From. Mail sent from an attacker's own domain passes
    SPF for that domain while carrying `From: someone@allowlisted.example`.

    So: DMARC pass, which checks alignment by definition; or an SPF/DKIM pass
    whose domain this function checks is aligned with From itself, for
    receivers that report no dmarc= result of their own.
    """
    auth_results = msg.get("Authentication-Results", "")
    if not auth_results:
        return False
    auth_lower = auth_results.lower()
    if re.search(r"\bdmarc\s*=\s*pass\b", auth_lower):
        return True
    from_domain = _domain_of(_extract_email_address(_decode_header_value(msg.get("From", ""))))
    if not from_domain:
        return False
    # Each mechanism is paired with the identity it actually authenticated;
    # only a result whose identity aligns with From counts.
    for mechanism, identities in (("spf", ("smtp.mailfrom", "smtp.helo")),
                                  ("dkim", ("header.d", "header.i"))):
        if not re.search(r"\b" + mechanism + r"\s*=\s*pass\b", auth_lower):
            continue
        for identity in identities:
            for match in re.finditer(
                    re.escape(identity) + r"\s*=\s*([^\s;,()]+)", auth_lower):
                if _domains_aligned(_domain_of(match.group(1)), from_domain):
                    return True
    return False


def _extract_text_body(msg: email_lib.message.Message) -> str:
    """Extract the plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    import re
                    return re.sub(r"<[^>]+>", "", html).strip()
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""


class EmailAdapter(BaseChannelAdapter):
    platform = "email"

    def __init__(self, config: dict, runner):
        self.config = config
        self.runner = runner
        self._address = A.get_secret(config, "email_address", "EMAIL_ADDRESS")
        self._password = A.get_secret(config, "email_password", "EMAIL_PASSWORD")
        self._smtp_host = A.get_secret(config, "email_smtp_host", "EMAIL_SMTP_HOST")
        self._smtp_port = int(config.get("email_smtp_port") or "587")
        self._imap_host = A.get_secret(config, "email_imap_host", "EMAIL_IMAP_HOST")
        self._imap_port = int(config.get("email_imap_port") or "993")
        self._running = False
        # Insertion-ordered dict, not a set: next(iter(set)) evicted an arbitrary
        # uid under load (audit L12). Oldest uid falls out first now.
        self._seen_uids: dict = {}
        self._thread_context: dict = {}  # sender → {subject, message_id}
        self._poll_task = None
        self._dispatch_futures: set = set()
        self._loop = None  # main event loop, captured in connect()
        self._verify_sender_enabled = str(
            config.get("email_verify_sender", "1")
        ).strip().lower() in ("1", "true", "yes", "on")

    async def connect(self) -> None:
        if not self._address or not self._password or not self._smtp_host or not self._imap_host:
            logger.error("Email: email_address, email_password, email_smtp_host, and email_imap_host required")
            return
        self._loop = asyncio.get_event_loop()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_inbox())
        logger.info("Email: polling %s every %ds", self._imap_host, int(POLL_INTERVAL))

    async def disconnect(self) -> None:
        self._running = False
        for future in tuple(self._dispatch_futures):
            future.cancel()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    async def _poll_inbox(self) -> None:
        """Poll IMAP for unread messages."""
        failures = 0
        while self._running:
            try:
                await asyncio.to_thread(self._check_inbox)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                # Failures used to log at debug forever, so a wrong password left
                # the platform silently dead (audit M10). Warn once it sticks,
                # and back off instead of hammering a dead server every 15s.
                if failures >= 3:
                    logger.warning("Email poll failed %d times in a row (%s)", failures, e)
                else:
                    logger.debug("Email poll error: %s", e)
            delay = POLL_INTERVAL
            if failures >= 3:
                delay = POLL_INTERVAL * min(2 ** (failures - 2), 8)
            await asyncio.sleep(delay)

    def _check_inbox(self) -> None:
        """Connect to IMAP, fetch unread messages, dispatch to runner."""
        import imaplib

        imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=30)
        try:
            imap.login(self._address, self._password)
            imap.select("INBOX")
            status, data = imap.uid("search", None, "UNSEEN")
            if status != "OK":
                return
            uids = data[0].split() if data[0] else []
            for uid_b in uids:
                uid = uid_b.decode()
                if uid in self._seen_uids:
                    continue
                self._seen_uids[uid] = None
                while len(self._seen_uids) > SEEN_UID_CAP:
                    self._seen_uids.pop(next(iter(self._seen_uids)))
                status, msg_data = imap.uid("fetch", uid_b, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw_email = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw_email)
                # One message must not be able to abort the batch (audit M11):
                # a malformed From (or anything else in one email) used to raise
                # out of the uid loop and silently lose the rest of the inbox.
                try:
                    self._process_message(msg)
                except Exception:
                    logger.exception("Email: failed to process message uid=%s", uid)
                # Consume it: UNSEEN search would otherwise re-dispatch the same
                # mail after every restart (audit L12).
                try:
                    imap.uid("store", uid_b, "+FLAGS", "(\\Seen)")
                except Exception:
                    pass
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def _process_message(self, msg: email_lib.message.Message) -> None:
        """Parse, verify, and dispatch a single email message."""
        from_header = _decode_header_value(msg.get("From", ""))
        from_addr = _extract_email_address(from_header)
        if not from_addr:
            return
        if from_addr == self._address.lower():
            return
        if _is_automated_sender(from_addr, msg):
            return

        # Authentication must precede the spoofable From-header allowlist.
        if self._verify_sender_enabled and not _verify_sender(msg):
            logger.warning("Email: rejected unverified sender %s (SPF/DKIM/DMARC failed)", from_addr)
            return

        # Allowlist check — before any dispatch. Unauthorized emails are silent.
        if self.runner and hasattr(self.runner, "allowlist"):
            if not self.runner.allowlist.is_allowed(from_addr, "email"):
                return

        # Collapse to one line: a decoded-word can unpack to embedded \r\n and
        # a header containing "name:" then makes as_string() raise, which turned
        # the whole outbound reply into a silent "0" (audit L6).
        subject = " ".join(_decode_header_value(msg.get("Subject", "")).split())
        message_id = msg.get("Message-ID", "")
        body = _extract_text_body(msg)
        if not body.strip():
            return
        text = body.strip()
        if subject and not subject.lower().startswith("re:"):
            text = f"[Subject: {subject}]\n\n{text}"
        self._thread_context[from_addr] = {"subject": subject, "message_id": message_id}
        event = MessageEvent(
            platform="email", chat_id=from_addr, chat_type="private",
            user_id=from_addr, text=text, attachments=[],
            thread_id=message_id, reply_to_message_id=message_id,
            raw={"email": {"from": from_header, "subject": subject, "message_id": message_id}},
        )
        logger.info("Email: received message from %s (%d chars)", from_addr, len(text))
        if self._loop is None:
            logger.warning("Email: cannot dispatch message before connect()")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.runner.on_message(event), self._loop
            )
            self._dispatch_futures.add(future)

            def report_dispatch(future):
                self._dispatch_futures.discard(future)
                try:
                    future.result()
                except Exception:
                    logger.exception("Email: agent turn failed for %s", from_addr)

            future.add_done_callback(report_dispatch)
        except Exception as e:
            logger.warning("Email: failed to dispatch message from %s: %s", from_addr, e)

    async def send_message(self, chat_id: str, text: str, **meta) -> str:
        """Send an email reply via SMTP."""
        if not self._smtp_host:
            return "0"
        body = text[:MAX_MESSAGE_LENGTH]
        ctx = self._thread_context.get(chat_id, {})
        subject = ctx.get("subject", "")
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        elif not subject:
            subject = "Agent8088 Reply"
        reply_to_id = meta.get("reply_to") or ctx.get("message_id", "")
        try:
            result = await asyncio.to_thread(
                self._send_email, chat_id, subject, body, reply_to_id
            )
            return result
        except Exception as e:
            logger.warning("Email send failed: %s", e)
            return "0"

    def _send_email(self, to_addr: str, subject: str, body: str, reply_to_id: str) -> str:
        """Send an email via SMTP.

        Tries the configured port first. If the configured port is 587
        (STARTTLS) and the connection times out — a common failure when an
        ISP or corporate firewall blackholes outbound SMTP-on-587 while
        leaving 465 (implicit SSL) open — falls back to port 465 once.
        """
        msg = MIMEMultipart()
        msg["From"] = self._address
        msg["To"] = to_addr
        msg["Subject"] = subject
        if reply_to_id:
            msg["In-Reply-To"] = reply_to_id
            msg["References"] = reply_to_id
        msg.attach(MIMEText(body, "plain", "utf-8"))

        ports_to_try = [self._smtp_port]
        # ponytail: 587 is frequently blackholed by ISPs; 465 (implicit SSL)
        # is the standard fallback. Drop this fallback if a configurable
        # port is explicitly set to something other than 587.
        if self._smtp_port == 587:
            ports_to_try.append(465)

        last_exc = None
        for port in ports_to_try:
            try:
                self._smtp_send_on_port(msg, port)
                logger.info("Email: sent reply to %s via SMTP %s:%d (%d chars)",
                            to_addr, self._smtp_host, port, len(body))
                return "1"
            except (smtplib.SMTPConnectError, socket.timeout, TimeoutError, OSError) as e:
                last_exc = e
                logger.warning("Email: SMTP %s:%d failed (%s), %s",
                               self._smtp_host, port, e,
                               "falling back to 465" if port != ports_to_try[-1]
                               else "no more ports to try")
                continue
        logger.warning("Email send failed: %s", last_exc)
        return "0"

    def _smtp_send_on_port(self, msg, port: int) -> None:
        """Connect to SMTP on the given port, login, send, and quit."""
        smtp = None
        try:
            if port == 465:
                smtp = smtplib.SMTP_SSL(self._smtp_host, port, timeout=30)
            else:
                smtp = smtplib.SMTP(self._smtp_host, port, timeout=30)
                smtp.starttls()
            smtp.login(self._address, self._password)
            smtp.send_message(msg)
        finally:
            if smtp:
                try:
                    smtp.quit()
                except Exception:
                    pass

    async def edit_message(self, chat_id: str, msg_id: str, text: str) -> None:
        pass  # emails cannot be edited

    async def on_message(self, event: MessageEvent) -> None:
        await self.runner.on_message(event)
