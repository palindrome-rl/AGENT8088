"""write_file's source_url arg: fetch and save binary/remote content directly,
instead of forcing the model to shell out and write ad-hoc download scripts.
"""
import json

import pytest


def _write(engine, filename, **extra):
    args = {"filename": filename, **extra}
    return engine.exec_tool("write_file", json.dumps(args))


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


def test_content_path_still_works_unchanged(ready, tmp_path):
    out = tmp_path / "note.txt"
    result = _write(ready, str(out), content="hello world")
    assert "hello world" not in result  # result is a summary, not an echo
    assert out.exists()
    assert out.read_text() == "hello world"


def test_source_url_fetches_and_saves_bytes(ready, tmp_path, monkeypatch):
    payload = bytes([0xFF, 0xD8, 0xFF, 0x00, 0x01, 0x02])  # not valid UTF-8
    monkeypatch.setattr(ready, "_fetch_url_bytes", lambda url: (payload, None, "image/jpeg"))
    out = tmp_path / "image.jpg"
    result = _write(ready, str(out), source_url="https://example.com/image.jpg")
    assert "fetched from https://example.com/image.jpg" in result
    assert out.exists()
    assert out.read_bytes() == payload


def test_source_url_respects_ssrf_guard(ready, tmp_path):
    out = tmp_path / "blocked.bin"
    result = _write(ready, str(out), source_url="http://192.168.1.1/x")
    assert "internal" in result.lower() or "not allowed" in result.lower()
    assert not out.exists()


def test_source_url_fetch_error_returns_message_no_file(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(ready, "_fetch_url_bytes", lambda url: (None, "Fetch failed: boom", None))
    out = tmp_path / "missing.bin"
    result = _write(ready, str(out), source_url="https://example.com/x")
    assert result == "Fetch failed: boom"
    assert not out.exists()


def test_content_takes_precedence_over_source_url(ready, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ready, "_fetch_url_bytes", lambda url: calls.append(url) or (b"x", None, "text/plain"))
    out = tmp_path / "text.txt"
    _write(ready, str(out), content="text wins", source_url="https://example.com/x")
    assert calls == []  # fetch never attempted
    assert out.read_text() == "text wins"


def test_oversized_source_url_response(ready, tmp_path, monkeypatch):
    message = f"Remote file exceeded the {ready.MAX_HTTP_BYTES}-byte limit."
    monkeypatch.setattr(ready, "_fetch_url_bytes", lambda url: (None, message, None))
    out = tmp_path / "big.bin"
    result = _write(ready, str(out), source_url="https://example.com/big.bin")
    assert result == message
    assert not out.exists()


def test_extensionless_filename_gets_extension_from_url(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(ready, "_fetch_url_bytes",
                       lambda url: (b"jpegbytes", None, "image/jpeg"))
    result = _write(ready, str(tmp_path / "image"), source_url="https://example.com/pic.jpg")
    assert (tmp_path / "image.jpg").exists()
    assert "image.jpg" in result


def test_extensionless_filename_derives_extension_from_content_type(
        ready, tmp_path, monkeypatch):
    monkeypatch.setattr(ready, "_fetch_url_bytes",
                       lambda url: (b"pngbytes", None, "image/png"))
    result = _write(ready, str(tmp_path / "picture"), source_url="https://example.com/get?id=1")
    assert (tmp_path / "picture.png").exists()
    assert "picture.png" in result


def test_explicit_extension_is_kept(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(ready, "_fetch_url_bytes",
                       lambda url: (b"data", None, "image/jpeg"))
    _write(ready, str(tmp_path / "img.png"), source_url="https://example.com/x.bin")
    assert (tmp_path / "img.png").exists()
    assert not (tmp_path / "img.png.jpeg").exists()
