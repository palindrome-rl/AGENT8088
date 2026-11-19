"""Issue S40, web half: a pasted credential gets a notice frame in the web chat.

The CLI already warns (cli._warn_pasted_credential). The web bridge must do the
same, advisory only: the turn still runs, and the frame carries the masked
warning, never the secret itself.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent8088 import web_server  # noqa: E402

SECRET = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


class FakeSocket:
    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)


def _drive(monkeypatch, text):
    """Run _handle_chat with the turn body stubbed out; return (frames, turn_ran)."""
    S = SimpleNamespace(id="chat-1")
    ran = []

    async def fake_turn(ws, text, attachments, interrupt, handed_off, A, C, S):
        ran.append(text)

    monkeypatch.setattr(web_server, "_turn_session", lambda C, sid: S)
    monkeypatch.setattr(web_server, "_validated_attachments", lambda ids, A, C, S: [])
    monkeypatch.setattr(web_server, "_handle_chat_locked", fake_turn)
    ws = FakeSocket()
    asyncio.run(web_server._handle_chat(ws, {"type": "chat", "text": text,
                                             "session_id": "chat-1"}, None, None))
    return ws.frames, ran


def test_pasted_credential_sends_a_warning_notice(monkeypatch):
    frames, ran = _drive(monkeypatch, "here is my key " + SECRET)
    notices = [f for f in frames if f.get("type") == "notice"]
    assert len(notices) == 1, frames
    notice = notices[0]
    assert notice["level"] == "warning"
    assert notice["session_id"] == "chat-1"
    assert "credential" in notice["text"]
    assert SECRET not in notice["text"]
    assert "ghp_...6789" in notice["text"]


def test_notice_is_advisory_and_the_turn_still_runs(monkeypatch):
    _frames, ran = _drive(monkeypatch, "here is my key " + SECRET)
    assert ran == ["here is my key " + SECRET]


def test_clean_message_sends_no_notice(monkeypatch):
    frames, ran = _drive(monkeypatch, "summarise the release notes please")
    assert not [f for f in frames if f.get("type") == "notice"], frames
    assert ran == ["summarise the release notes please"]


def test_notice_frame_builder_masks_and_returns_none_when_clean():
    assert web_server._credential_notice("nothing to see here", "s") is None
    frame = web_server._credential_notice("key " + SECRET, "s")
    assert frame["type"] == "notice" and SECRET not in repr(frame)
