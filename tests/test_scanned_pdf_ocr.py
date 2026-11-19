"""Scanned PDF pages: OCR'd when OCR is installed (all pages, or only the ones
without a text layer), otherwise named with the install command; OCR and
LibreOffice gaps reach the capabilities registry and /doctor. OCR itself is
stubbed — no worker process, no model."""

import pytest

from agent8088 import capabilities, documents, ocr


def _pdf(tmp_path, texts, name="doc.pdf"):
    """A PDF whose pages carry `texts` ("" = no text layer, like a scan)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    path = tmp_path / name
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def ocr_on(monkeypatch):
    calls = []

    def _text_for(path, *, max_pages=ocr.MAX_OCR_PAGES, pages=None):
        calls.append(pages)
        return "## Page 2\nSCANNED WORDS" if pages else "## Page 1\nSCANNED WORDS"

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "text_for", _text_for)
    return calls


@pytest.fixture
def ocr_off(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)


def test_fully_scanned_pdf_is_ocrd(tmp_path, ocr_on):
    out = documents.extract_text(_pdf(tmp_path, [""]))
    assert ocr_on == [None]  # the whole document
    assert out.startswith("[Agent8088 OCR transcript — 1 of 1 page(s) had no text layer")
    assert "SCANNED WORDS" in out
    assert capabilities.get(capabilities.OCR).state == capabilities.OK


def test_fully_scanned_pdf_without_ocr_names_install(tmp_path, ocr_off):
    out = documents.extract_text(_pdf(tmp_path, ["", ""]))
    assert "no extractable text" in out and "OCR is not installed" in out
    assert "pip install" in out and "rapidocr" in out
    entry = capabilities.get(capabilities.OCR)
    assert entry.state == capabilities.UNAVAILABLE and "pip install" in entry.fix


def test_partly_scanned_pdf_ocrs_only_blank_pages(tmp_path, ocr_on):
    out = documents.extract_text(_pdf(tmp_path, ["Hello typed page", ""]))
    assert ocr_on == [[2]]
    assert "Hello typed page" in out
    assert "1 of 2 page(s) had no text layer" in out and "SCANNED WORDS" in out


def test_partly_scanned_pdf_without_ocr_says_what_was_dropped(tmp_path, ocr_off):
    out = documents.extract_text(_pdf(tmp_path, ["Hello typed page", "", ""]))
    assert "Hello typed page" in out
    assert "[note: 2 of 3 pages had no text (scanned): pages 2, 3; install OCR to read them:" in out


def test_text_pdf_untouched(tmp_path, ocr_on):
    out = documents.extract_text(_pdf(tmp_path, ["All typed"]))
    assert "All typed" in out and "OCR" not in out
    assert ocr_on == []


def test_bounded_worker_defers_ocr_to_caller(tmp_path, ocr_on):
    path = _pdf(tmp_path, ["Hello typed page", ""])
    deferred = documents.extract_text(path, ocr=False)
    assert ocr_on == []
    assert deferred.rstrip().endswith("[no text layer on pages 2 of 2]")
    finished = documents.ocr_scanned_pages(path, deferred)
    assert ocr_on == [[2]]
    assert "[no text layer" not in finished and "SCANNED WORDS" in finished


def test_document_access_load_runs_ocr_after_worker(tmp_path, ocr_on, monkeypatch):
    import subprocess
    import types

    from agent8088 import document_access
    path = _pdf(tmp_path, [""])
    worker_out = documents.extract_text(path, ocr=False).encode()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=worker_out, stderr=b""))
    text = document_access.load(path)
    assert "SCANNED WORDS" in text


def test_ocr_failure_is_reported_inline(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)

    def _boom(path, **kwargs):
        raise ValueError("OCR timed out after 180s on doc.pdf")

    monkeypatch.setattr(ocr, "text_for", _boom)
    out = documents.extract_text(_pdf(tmp_path, ["typed", ""]))
    assert "[OCR of the 1 scanned page(s) failed: OCR timed out" in out


def test_text_for_passes_pages_to_worker(tmp_path, monkeypatch):
    import subprocess
    import types
    monkeypatch.setattr(ocr, "available", lambda: True)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return types.SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = tmp_path / "s.pdf"
    path.write_bytes(b"%PDF-1.4")
    ocr.text_for(path, pages=[5, 2])
    assert seen["argv"][-2:] == ["--pages", "2,5"]
    assert seen["timeout"] == ocr.OCR_TIMEOUT_SECONDS


def test_pdf_text_only_selected_pages(monkeypatch, tmp_path):
    import types

    class _Bitmap:
        def to_pil(self):
            return "image"

    class _Page:
        def render(self, scale):
            return _Bitmap()

    class _Doc:
        def __init__(self, path):
            pass

        def __len__(self):
            return 5

        def __getitem__(self, index):
            return _Page()

        def close(self):
            pass

    monkeypatch.setattr(ocr, "_pdfium", lambda: types.SimpleNamespace(PdfDocument=_Doc))
    monkeypatch.setattr(ocr, "_engine", lambda: (lambda image: types.SimpleNamespace(txts=["x"])))
    out = ocr._pdf_text(tmp_path / "s.pdf", only=[2, 4])
    assert "## Page 2" in out and "## Page 4" in out and "## Page 1" not in out


def test_missing_libreoffice_reports_documents_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "_soffice_executable", lambda: None)
    src = tmp_path / "a.docx"
    src.write_bytes(b"x")
    out = documents.convert_document(src, "pdf")
    assert "LibreOffice is not installed" in out
    entry = capabilities.get(capabilities.DOCUMENTS)
    assert entry.state == capabilities.DEGRADED and "convert_document" in entry.impact


def test_report_tooling_is_cheap_and_reports_both(monkeypatch):
    monkeypatch.setattr(documents, "_soffice_executable", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(ocr, "installed", lambda: False)
    monkeypatch.setattr(ocr, "_import_engines",
                        lambda: (_ for _ in ()).throw(AssertionError("must not import")))
    documents.report_tooling()
    assert capabilities.get(capabilities.DOCUMENTS).state == capabilities.OK
    assert capabilities.get(capabilities.OCR).state == capabilities.UNAVAILABLE


def test_doctor_has_ocr_and_documents_rows(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(documents, "_soffice_executable", lambda: None)
    monkeypatch.setattr(ocr, "installed", lambda: True)
    rows = {c["name"]: c for c in cli._doctor_document_checks()}
    assert rows["OCR"]["status"] == "ok"
    assert rows["Document conversion"]["status"] == "warn"
    assert "libreoffice" in rows["Document conversion"]["fix"].lower()
