"""Web chat image attachments must reach a vision model as real image parts,
built by the same engine.build_image_message the terminal's /image uses.
Before, the web only appended a text list of attachment paths, so the model
never saw the image at all."""
import asyncio
import json
import types
from pathlib import Path

from agent8088 import cli, engine, web_server

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
       b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


class _Engine:
    class AgentInterrupted(Exception):
        pass

    build_image_message = staticmethod(engine.build_image_message)
    _message_text = staticmethod(engine._message_text)

    def __init__(self, root):
        self.PERMISSION_MODE = "full-auto"
        self.ARTIFACTS_ROOT = root
        self.sent = None

    def run_agent(self, messages, **kwargs):
        self.sent = [dict(m) for m in messages]
        kwargs["on_answer"]("done")
        return "done"

    def current_rate_limit_status(self, provider):
        return None


def _turn(monkeypatch, tmp_path, name, data, vision):
    monkeypatch.setattr(cli, "S", cli.Session())
    folder = tmp_path / ".web-attachments" / "web-session"
    folder.mkdir(parents=True)
    path = folder / ("a" * 32 + Path(name).suffix)
    path.write_bytes(data)
    attachment = {"id": "a" * 32, "name": name, "path": str(path), "warning": ""}
    monkeypatch.setattr(web_server, "_validated_attachments", lambda *a, **k: [attachment])
    monkeypatch.setattr(web_server, "_vision_capable_probed", lambda: vision)
    monkeypatch.setattr(web_server, "_ocr_module", lambda: types.SimpleNamespace(
        text_for=lambda p, **k: "OCR WORDS",
        needs_ocr=lambda p: Path(p).suffix.lower() in web_server._ATTACHMENT_IMAGE_EXTENSIONS))
    agent = _Engine(tmp_path)
    asyncio.run(web_server._handle_chat(_Socket(), {"text": "what is this?"}, agent, cli))
    return agent.sent[-1]


def test_a_vision_model_gets_the_image_itself(monkeypatch, tmp_path):
    message = _turn(monkeypatch, tmp_path, "shot.png", PNG, vision=True)
    parts = message["content"]
    assert isinstance(parts, list)
    urls = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
    assert len(urls) == 1 and urls[0].startswith("data:image/png;base64,")
    text = parts[0]["text"]
    assert text.startswith("what is this?")
    assert "included in this message as an image" in text
    assert "visual tools" not in text


def test_a_model_without_vision_falls_back_to_ocr_text(monkeypatch, tmp_path):
    message = _turn(monkeypatch, tmp_path, "shot.png", PNG, vision=False)
    assert isinstance(message["content"], str)
    assert "OCR WORDS" in message["content"]
    assert "visual tools" not in message["content"]


def test_a_non_image_attachment_stays_text_context(monkeypatch, tmp_path):
    message = _turn(monkeypatch, tmp_path, "notes.txt", b"hello", vision=True)
    assert isinstance(message["content"], str)
    assert "notes.txt (attachment " in message["content"]


def test_history_and_saved_session_survive_image_content(monkeypatch, tmp_path):
    message = _turn(monkeypatch, tmp_path, "shot.png", PNG, vision=True)
    cli.S.messages = [message, {"role": "assistant", "content": "a pixel"}]
    monkeypatch.setattr(web_server, "_cl", lambda: cli)
    history = asyncio.run(web_server.get_history())
    shown = history["messages"][0]
    assert shown["content"] == "what is this?"
    assert shown["attachments"] == [{"id": "a" * 32, "name": "shot.png"}]
    assert "base64" not in json.dumps(history)
    # Save/resume is plain JSON and the engine's text readers see only text.
    assert json.loads(json.dumps(cli.S.messages))[0]["content"] == message["content"]
    assert engine._preflight_refusal(cli.S.messages[:1]) is None
    assert "base64" not in engine._message_text(message)


def test_upload_of_an_uncatalogued_model_is_unconfirmed_not_unreadable(monkeypatch, tmp_path):
    from starlette.requests import Request

    monkeypatch.setattr(web_server, "_eng", lambda: types.SimpleNamespace(ARTIFACTS_ROOT=tmp_path))
    monkeypatch.setattr(web_server, "_cl", lambda: types.SimpleNamespace(S=types.SimpleNamespace(name="s")))
    monkeypatch.setattr(web_server, "_vision_capable", lambda: None)
    monkeypatch.setattr(web_server, "_ocr_module", lambda: types.SimpleNamespace(available=lambda: False))

    async def receive():
        return {"type": "http.request", "body": PNG, "more_body": False}

    result = asyncio.run(web_server.upload_attachment(
        Request({"type": "http", "headers": [(b"x-filename", b"shot.png")]}, receive)))
    assert result["status"] == "ready"
    assert "unconfirmed" in result["warning"]
    assert "so this image cannot be read" not in result["warning"]
