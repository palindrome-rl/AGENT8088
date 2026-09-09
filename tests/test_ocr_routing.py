"""Automatic OCR routing: only models without vision get OCR text.

Covers the capability gate itself, the web attachment path, and the CLI
image commands. No real OCR engine is involved anywhere in this file.
"""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import model_catalog


# --- the capability gate ----------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_catalog():
    model_catalog.reload_catalog()
    yield
    model_catalog.reload_catalog()


def test_a_catalogued_vision_model_is_capable():
    assert model_catalog.vision_capable("gemini", "gemini-2.5-pro") is True


def test_a_catalogued_text_only_model_is_not_capable():
    assert model_catalog.vision_capable("deepseek", "deepseek-chat") is False


def test_an_unknown_model_is_assumed_to_have_no_vision():
    """Local Ollama and custom endpoints are absent from the catalog. Assuming
    no vision gives them OCR text, which a vision model could still read; the
    reverse hands a blind model image bytes it cannot use at all."""
    assert model_catalog.vision_capable("ollama", "some-local-build") is False
    assert model_catalog.vision_capable("custom", "internal-endpoint-v2") is False


def test_a_user_override_can_declare_vision_on_a_local_model(tmp_path, monkeypatch):
    user_file = tmp_path / "model_catalog.json"
    user_file.write_text(json.dumps({
        "ollama:llava-next": {"rank": 40, "vision": True, "tools": False},
    }), encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_user_catalog_path", lambda: user_file)
    model_catalog.reload_catalog()
    assert model_catalog.vision_capable("ollama", "llava-next") is True


def test_a_malformed_vision_value_is_not_treated_as_capable(tmp_path, monkeypatch):
    user_file = tmp_path / "model_catalog.json"
    user_file.write_text(json.dumps({"ollama:odd": {"rank": 40}}), encoding="utf-8")
    monkeypatch.setattr(model_catalog, "_user_catalog_path", lambda: user_file)
    model_catalog.reload_catalog()
    assert model_catalog.vision_capable("ollama", "odd") is False


# --- the web attachment path ------------------------------------------------

from agent8088 import web_server as web


def _attachment(tmp_path, name="shot.png", warning=""):
    target = tmp_path / name
    target.write_bytes(b"bytes")
    return {"id": "a" * 32, "name": name, "path": str(target), "warning": warning}


def _stub_ocr(monkeypatch, text="RECOGNISED TEXT", error=None):
    calls = []

    def text_for(path, **kwargs):
        calls.append(str(path))
        if error:
            raise ValueError(error)
        return text

    monkeypatch.setattr(web, "_ocr_module", lambda: types.SimpleNamespace(
        text_for=text_for, needs_ocr=lambda p: Path(p).suffix.lower() in
        {".png", ".jpg", ".jpeg", ".gif", ".webp"}))
    return calls


def test_a_blind_model_gets_ocr_text_for_an_image(monkeypatch, tmp_path):
    calls = _stub_ocr(monkeypatch)
    monkeypatch.setattr(web, "_vision_capable", lambda: False)
    context = web._attachment_ocr_context([_attachment(tmp_path)])
    assert "RECOGNISED TEXT" in context
    assert len(calls) == 1


def test_a_vision_model_gets_no_ocr_and_the_engine_is_never_called(monkeypatch, tmp_path):
    calls = _stub_ocr(monkeypatch)
    monkeypatch.setattr(web, "_vision_capable", lambda: True)
    assert web._attachment_ocr_context([_attachment(tmp_path)]) == ""
    assert calls == []


def test_a_scanned_pdf_is_ocrd_only_once_extraction_has_come_up_empty(monkeypatch, tmp_path):
    calls = _stub_ocr(monkeypatch)
    monkeypatch.setattr(web, "_vision_capable", lambda: False)
    scanned = _attachment(tmp_path, "scan.pdf", warning="This PDF has no extractable text")
    assert "RECOGNISED TEXT" in web._attachment_ocr_context([scanned])
    assert len(calls) == 1


def test_a_text_bearing_pdf_is_left_to_document_read(monkeypatch, tmp_path):
    calls = _stub_ocr(monkeypatch)
    monkeypatch.setattr(web, "_vision_capable", lambda: False)
    readable = _attachment(tmp_path, "report.pdf")
    assert web._attachment_ocr_context([readable]) == ""
    assert calls == []


def test_ocr_failure_is_reported_to_the_model_not_swallowed(monkeypatch, tmp_path):
    _stub_ocr(monkeypatch, error="OCR is not installed")
    monkeypatch.setattr(web, "_vision_capable", lambda: False)
    context = web._attachment_ocr_context([_attachment(tmp_path)])
    assert "could not be read" in context
    assert "OCR is not installed" in context


def test_the_ocr_block_names_the_file_and_frames_it_as_untrusted(monkeypatch, tmp_path):
    _stub_ocr(monkeypatch)
    monkeypatch.setattr(web, "_vision_capable", lambda: False)
    context = web._attachment_ocr_context([_attachment(tmp_path, "invoice.png")])
    assert "invoice.png" in context
    assert "untrusted" in context.lower()


def test_one_failing_attachment_does_not_lose_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_vision_capable", lambda: False)

    def text_for(path, **kwargs):
        if "bad" in str(path):
            raise ValueError("engine exploded")
        return "GOOD TEXT"

    monkeypatch.setattr(web, "_ocr_module", lambda: types.SimpleNamespace(
        text_for=text_for, needs_ocr=lambda p: True))
    context = web._attachment_ocr_context(
        [_attachment(tmp_path, "bad.png"), _attachment(tmp_path, "good.png")])
    assert "GOOD TEXT" in context
    assert "engine exploded" in context


# --- the CLI image commands -------------------------------------------------

def test_cmd_image_sends_ocr_text_when_the_model_cannot_see(monkeypatch, tmp_path):
    from agent8088 import cli

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"bytes")
    monkeypatch.setattr(cli, "_vision_capable", lambda: False)
    monkeypatch.setattr(cli.A, "resolve_pasted_path", lambda ref: Path(ref))
    monkeypatch.setattr(cli, "_ocr_module", lambda: types.SimpleNamespace(
        text_for=lambda path, **k: "OCR CONTENT", needs_ocr=lambda p: True))

    def unexpected(*a, **k):
        raise AssertionError("a blind model must not be sent image parts")

    monkeypatch.setattr(cli.A, "build_image_message", unexpected)
    message = cli._image_user_message("What is this?", str(shot))
    assert isinstance(message["content"], str)
    assert "OCR CONTENT" in message["content"]
    assert "What is this?" in message["content"]


def test_cmd_image_keeps_the_multimodal_path_for_a_vision_model(monkeypatch, tmp_path):
    from agent8088 import cli

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"bytes")
    monkeypatch.setattr(cli, "_vision_capable", lambda: True)
    monkeypatch.setattr(cli, "_ocr_module", lambda: types.SimpleNamespace(
        text_for=lambda path, **k: (_ for _ in ()).throw(AssertionError("no OCR here")),
        needs_ocr=lambda p: True))
    monkeypatch.setattr(cli.A, "build_image_message",
                        lambda text, refs, resolver=None: {"role": "user", "content": [{"type": "text", "text": text}]})
    message = cli._image_user_message("What is this?", str(shot))
    assert isinstance(message["content"], list)


# --- upload-time feedback ---------------------------------------------------

def test_an_image_upload_warns_when_ocr_could_not_run(monkeypatch, tmp_path):
    """The chip must say so at upload; discovering it after the turn is worse."""
    import asyncio
    from types import SimpleNamespace
    from starlette.requests import Request

    monkeypatch.setattr(web, "_eng", lambda: SimpleNamespace(ARTIFACTS_ROOT=tmp_path))
    monkeypatch.setattr(web, "_cl", lambda: SimpleNamespace(S=SimpleNamespace(name="s")))
    monkeypatch.setattr(web, "_vision_capable", lambda: False)
    monkeypatch.setattr(web, "_ocr_module", lambda: types.SimpleNamespace(available=lambda: False))

    async def receive():
        return {"type": "http.request", "body": b"bytes", "more_body": False}

    result = asyncio.run(web.upload_attachment(
        Request({"type": "http", "headers": [(b"x-filename", b"shot.png")]}, receive)))
    assert result["status"] == "needs_attention"
    assert "OCR engine is not" in result["warning"]


def test_an_image_upload_is_ready_when_ocr_is_available(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from starlette.requests import Request

    monkeypatch.setattr(web, "_eng", lambda: SimpleNamespace(ARTIFACTS_ROOT=tmp_path))
    monkeypatch.setattr(web, "_cl", lambda: SimpleNamespace(S=SimpleNamespace(name="s")))
    monkeypatch.setattr(web, "_vision_capable", lambda: False)
    monkeypatch.setattr(web, "_ocr_module", lambda: types.SimpleNamespace(available=lambda: True))

    async def receive():
        return {"type": "http.request", "body": b"bytes", "more_body": False}

    result = asyncio.run(web.upload_attachment(
        Request({"type": "http", "headers": [(b"x-filename", b"shot.png")]}, receive)))
    assert result["status"] == "ready"
    assert not result.get("warning")


def test_a_vision_model_never_probes_the_ocr_engine_on_upload(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from starlette.requests import Request

    monkeypatch.setattr(web, "_eng", lambda: SimpleNamespace(ARTIFACTS_ROOT=tmp_path))
    monkeypatch.setattr(web, "_cl", lambda: SimpleNamespace(S=SimpleNamespace(name="s")))
    monkeypatch.setattr(web, "_vision_capable", lambda: True)

    def unexpected():
        raise AssertionError("a vision model must not pay for an OCR probe")

    monkeypatch.setattr(web, "_ocr_module", lambda: types.SimpleNamespace(available=unexpected))

    async def receive():
        return {"type": "http.request", "body": b"bytes", "more_body": False}

    result = asyncio.run(web.upload_attachment(
        Request({"type": "http", "headers": [(b"x-filename", b"shot.png")]}, receive)))
    assert result["status"] == "ready"
