import base64

import pytest


def test_build_image_message_encodes_local_file(engine, tmp_path, monkeypatch):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    monkeypatch.setattr(engine, "resolve_user_path", lambda r: img)
    msg = engine.build_image_message("what is this?", [str(img)])
    assert msg["role"] == "user"
    parts = msg["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"\x89PNG\r\n\x1a\nFAKE"


def test_build_image_message_passes_through_remote_url(engine):
    msg = engine.build_image_message("describe", ["https://example.com/a.jpg"])
    assert msg["content"][1]["image_url"]["url"] == "https://example.com/a.jpg"


def test_build_image_message_rejects_missing_file(engine, tmp_path, monkeypatch):
    missing = tmp_path / "nope.png"
    monkeypatch.setattr(engine, "resolve_user_path", lambda r: missing)
    with pytest.raises(ValueError, match="not found"):
        engine.build_image_message("x", [str(missing)])


def test_build_image_message_ssrf_guards_remote(engine, monkeypatch):
    monkeypatch.setattr(engine, "SSRF_ALLOW_PRIVATE", False)
    with pytest.raises(ValueError, match="Blocked"):
        engine.build_image_message("x", ["http://169.254.169.254/img.png"])


def test_build_image_message_infers_jpeg_mime(engine, tmp_path, monkeypatch):
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(engine, "resolve_user_path", lambda r: img)
    msg = engine.build_image_message("q", [str(img)])
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
