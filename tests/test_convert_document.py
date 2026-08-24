"""documents.convert_document — the deterministic LibreOffice conversion path.

Exists because skill-only guidance ("run soffice via execute_shell") failed
against the actual target model twice in manual testing: asked to convert an
existing .docx to PDF, it wrote a fresh reportlab script generating an
unrelated document instead of using the documented soffice command. A
deterministic tool removes the chance to substitute a different approach.

subprocess.run is monkeypatched throughout — these tests never need a real
LibreOffice install, and must still pass in CI/dev environments without one.
"""
import subprocess

import pytest

from agent8088 import documents


def test_unsupported_target_format_is_refused_before_touching_soffice(monkeypatch, tmp_path):
    monkeypatch.setattr(documents, "_soffice_executable", lambda: None)  # would fail loudly if reached
    result = documents.convert_document(tmp_path / "in.docx", "epub")
    assert "epub" in result
    assert "Supported targets" in result


def test_missing_libreoffice_gives_an_actionable_message(monkeypatch, tmp_path):
    monkeypatch.setattr(documents, "_soffice_executable", lambda: None)
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")
    result = documents.convert_document(src, "pdf")
    assert "not installed" in result
    assert "winget install TheDocumentFoundation.LibreOffice" in result


def test_missing_source_file_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(documents, "_soffice_executable", lambda: "soffice")
    result = documents.convert_document(tmp_path / "nope.docx", "pdf")
    assert "does not exist" in result


def test_successful_conversion_reports_the_output_file(monkeypatch, tmp_path):
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")
    monkeypatch.setattr(documents, "_soffice_executable", lambda: "soffice")

    def fake_run(argv, **kwargs):
        # soffice --convert-to writes to --outdir with the same basename,
        # new extension -- simulate that side effect the way the real
        # binary would, so the disk-check in convert_document sees it.
        (tmp_path / "report.pdf").write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(argv, 0, stdout="convert ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = documents.convert_document(src, "pdf")
    assert "Converted report.docx to report.pdf" in result
    assert "bytes" in result


def test_soffice_runs_but_produces_nothing_is_a_failure_not_a_silent_success(monkeypatch, tmp_path):
    """soffice can print a success-looking line and still not write the file
    (locked output, unsupported filter) -- the disk state is the only source
    of truth, not stdout text or exit code."""
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")
    monkeypatch.setattr(documents, "_soffice_executable", lambda: "soffice")
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="", stderr="filter error"),
    )
    result = documents.convert_document(src, "pdf")
    assert "Conversion failed" in result
    assert "filter error" in result


def test_timeout_gives_a_clear_message_not_a_traceback(monkeypatch, tmp_path):
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")
    monkeypatch.setattr(documents, "_soffice_executable", lambda: "soffice")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = documents.convert_document(src, "pdf", timeout=5)
    assert "timed out" in result


def test_target_format_accepts_a_leading_dot_and_mixed_case(monkeypatch, tmp_path):
    """Model-supplied args are free text -- '.PDF' and 'pdf' should behave
    identically rather than one silently failing validation."""
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")
    monkeypatch.setattr(documents, "_soffice_executable", lambda: None)  # fails past validation, not on it
    result = documents.convert_document(src, ".PDF")
    assert "Supported targets" not in result  # validation passed
    assert "not installed" in result  # reached the soffice-missing branch


def test_pdf_to_docx_is_rejected_before_running_soffice(monkeypatch, tmp_path):
    """LibreOffice headless opens PDF as a Draw document and has no export
    filter for Writer/Impress/Calc formats — so PDF -> docx/pptx/xlsx fails
    every time with 'no export filter'. Reject the combination upfront so the
    model gets an actionable message instead of a raw filter error it then
    tries to work around with a cascade of shell commands."""
    monkeypatch.setattr(documents, "_soffice_executable",
                        lambda: pytest.fail("must not reach soffice for an unsupported pair"))
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    result = documents.convert_document(src, "docx")
    assert "not supported" in result
    assert ".pdf -> .docx" in result
    assert "pdfplumber" in result  # points the model at the right tool


def test_pdf_to_pdf_round_trip_is_allowed(monkeypatch, tmp_path):
    """PDF -> PDF is a valid re-export (Draw exports to PDF). Don't reject it
    just because the source is PDF."""
    monkeypatch.setattr(documents, "_soffice_executable", lambda: None)
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    result = documents.convert_document(src, "pdf")
    assert "not supported" not in result  # reached the soffice-missing branch, not the combo reject
    assert "not installed" in result


def test_docx_to_pdf_is_allowed(monkeypatch, tmp_path):
    """The canonical conversion — must not be caught by the PDF-source guard."""
    monkeypatch.setattr(documents, "_soffice_executable", lambda: None)
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")
    result = documents.convert_document(src, "pdf")
    assert "not supported" not in result
    assert "not installed" in result
