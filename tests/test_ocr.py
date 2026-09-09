"""OCR engine module: text out of images and scanned PDFs.

The third-party engines (rapidocr, pypdfium2) are never imported by these
tests. Recognition is exercised through injected fakes and the subprocess
boundary is mocked, so the suite runs on a machine without the [ocr] extra
and makes no network calls.
"""
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import ocr


@pytest.fixture(autouse=True)
def _clear_cache():
    ocr._cache.clear()
    yield
    ocr._cache.clear()


def _png(tmp_path, name="shot.png", body=b"\x89PNG\r\n\x1a\nfake"):
    target = tmp_path / name
    target.write_bytes(body)
    return target


# --- availability -----------------------------------------------------------

def test_available_is_false_when_the_extra_is_missing(monkeypatch):
    monkeypatch.setattr(ocr, "_import_engines", lambda: (_ for _ in ()).throw(ImportError("no rapidocr")))
    assert ocr.available() is False


def test_available_is_true_when_both_engines_import(monkeypatch):
    monkeypatch.setattr(ocr, "_import_engines", lambda: (object(), object()))
    assert ocr.available() is True


def test_text_for_reports_a_actionable_message_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: False)
    with pytest.raises(ValueError, match="OCR is not installed"):
        ocr.text_for(_png(tmp_path))


# --- dispatch and validation ------------------------------------------------

def test_text_for_rejects_a_format_it_cannot_read(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    target = tmp_path / "notes.docx"
    target.write_bytes(b"zip")
    with pytest.raises(ValueError, match="cannot be read by OCR"):
        ocr.text_for(target)


def test_text_for_rejects_a_file_over_the_size_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "MAX_OCR_BYTES", 8)
    with pytest.raises(ValueError, match="too large"):
        ocr.text_for(_png(tmp_path, body=b"x" * 64))


def test_needs_ocr_covers_images_always_and_pdfs_never_on_their_own():
    assert ocr.needs_ocr(Path("a.png")) is True
    assert ocr.needs_ocr(Path("a.JPEG")) is True
    # A PDF is only an OCR candidate once text extraction has come up empty;
    # that judgement belongs to the caller, not to the suffix.
    assert ocr.needs_ocr(Path("a.pdf")) is False
    assert ocr.needs_ocr(Path("a.txt")) is False


# --- subprocess boundary ----------------------------------------------------

def test_text_for_returns_worker_output(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=b"INVOICE 2026\nTotal: 40", stderr=b""))
    assert ocr.text_for(_png(tmp_path)) == "INVOICE 2026\nTotal: 40"


def test_text_for_runs_the_worker_out_of_process_with_a_timeout(monkeypatch, tmp_path):
    """Untrusted input must not reach a native parser on the caller's thread."""
    monkeypatch.setattr(ocr, "available", lambda: True)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return types.SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ocr.text_for(_png(tmp_path))
    assert seen["argv"][:3] == [sys.executable, "-m", "agent8088.ocr"]
    assert seen["timeout"] == ocr.OCR_TIMEOUT_SECONDS


def test_text_for_surfaces_worker_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout=b"", stderr=b"engine exploded"))
    with pytest.raises(ValueError, match="engine exploded"):
        ocr.text_for(_png(tmp_path))


def test_text_for_reports_a_timeout_rather_than_hanging(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ocr", timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError, match="timed out"):
        ocr.text_for(_png(tmp_path))


def test_text_for_treats_an_empty_result_as_a_failure(monkeypatch, tmp_path):
    """An image with no readable text must say so, not return "" silently."""
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=b"   \n  ", stderr=b""))
    with pytest.raises(ValueError, match="no readable text"):
        ocr.text_for(_png(tmp_path))


# --- caching ----------------------------------------------------------------

def test_repeat_calls_reuse_the_cached_text(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    calls = []

    def counted(*a, **k):
        calls.append(1)
        return types.SimpleNamespace(returncode=0, stdout=b"cached", stderr=b"")

    monkeypatch.setattr(subprocess, "run", counted)
    target = _png(tmp_path)
    assert ocr.text_for(target) == "cached"
    assert ocr.text_for(target) == "cached"
    assert len(calls) == 1


def test_discard_drops_the_cached_text(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr, "available", lambda: True)
    calls = []

    def counted(*a, **k):
        calls.append(1)
        return types.SimpleNamespace(returncode=0, stdout=b"cached", stderr=b"")

    monkeypatch.setattr(subprocess, "run", counted)
    target = _png(tmp_path)
    ocr.text_for(target)
    ocr.discard(target)
    ocr.text_for(target)
    assert len(calls) == 2


# --- worker-side recognition ------------------------------------------------

class _FakeResult:
    def __init__(self, texts):
        self.txts = texts


def _fake_engines(texts, pages=1):
    """(rapidocr_instance, pdfium_module) doubles matching the shapes used."""
    def engine(image):
        return _FakeResult(texts)


    class _Bitmap:
        def to_pil(self):
            return object()

    class _Page:
        def render(self, **kwargs):
            return _Bitmap()

    class _Document:
        def __init__(self, path):
            self.path = path

        def __len__(self):
            return pages

        def __getitem__(self, index):
            return _Page()

        def close(self):
            pass

    pdfium = types.SimpleNamespace(PdfDocument=_Document)
    return engine, pdfium


def test_image_text_joins_recognised_lines(monkeypatch):
    engine, _ = _fake_engines(["Line one", "Line two"])
    monkeypatch.setattr(ocr, "_engine", lambda: engine)
    assert ocr._image_text(Path("shot.png")) == "Line one\nLine two"


def test_image_text_is_empty_when_nothing_is_recognised(monkeypatch):
    engine, _ = _fake_engines([])
    monkeypatch.setattr(ocr, "_engine", lambda: engine)
    assert ocr._image_text(Path("blank.png")) == ""


def test_pdf_text_labels_each_page(monkeypatch, tmp_path):
    engine, pdfium = _fake_engines(["Page body"], pages=2)
    monkeypatch.setattr(ocr, "_engine", lambda: engine)
    monkeypatch.setattr(ocr, "_pdfium", lambda: pdfium)
    out = ocr._pdf_text(tmp_path / "scan.pdf", max_pages=10)
    assert "## Page 1" in out and "## Page 2" in out
    assert out.count("Page body") == 2


def test_pdf_text_stops_at_the_page_ceiling(monkeypatch, tmp_path):
    engine, pdfium = _fake_engines(["Body"], pages=50)
    monkeypatch.setattr(ocr, "_engine", lambda: engine)
    monkeypatch.setattr(ocr, "_pdfium", lambda: pdfium)
    out = ocr._pdf_text(tmp_path / "scan.pdf", max_pages=3)
    assert "## Page 3" in out
    assert "## Page 4" not in out
    assert "47 further pages" in out
