"""convert_document, the tool — engine-level wiring and permission gating.

Unit coverage for the conversion logic itself lives in
tests/test_convert_document.py; this file covers what only exists once the
tool is registered: mode=write_text sharing every write guard (same reasoning
as create_document — a dozen sites key on that mode, a private mode would
need every one added), and that the pipe-delimited tools.txt row parsed
correctly (a description containing '|' gets silently truncated — a real bug
hit once already this session on create_document's own row).
"""
import json

import pytest


def _convert(engine, path, fmt):
    return engine.exec_tool(
        "convert_document", json.dumps({"filename": str(path), "format": fmt}))


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


def test_convert_document_is_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["convert_document"]["mode"] == "write_text"


def test_convert_document_is_excluded_from_the_auditor(engine):
    """convert_document is a deterministic built-in that verifies its own output
    on disk (output_path.exists() + byte count). The auditor runs in a disposable
    sandbox copy and cannot see the real Windows file the step produced, so it
    returns fail/unknown from its own blindness — pure noise that costs a model
    call and can revert correct work. So even with plan_audit on and a write
    closure mode, this tool must not be audited."""
    assert engine._plan_step_is_auditable("convert_document", "") is False
    # A declared acceptance criterion is the strongest audit signal — but the
    # exclusion holds even there, because the tool's own disk check is the
    # real acceptance criterion and an auditor cannot improve on it.
    assert engine._plan_step_is_auditable("convert_document", "file exists") is False
    # The exclusion is specific, not a blanket mute: a write_file step with the
    # same mode still gets audited, so the guard cannot be widened by accident.
    assert engine._plan_step_is_auditable("write_file", "") is True


def test_its_description_survived_the_pipe_delimited_registry(engine):
    desc = engine.TOOL_SPECS["convert_document"]["description"]
    assert "LibreOffice" in desc, "description was cut short by a stray pipe"


def test_real_conversion_flows_through_to_documents_module(engine, tmp_path, monkeypatch):
    """Prove the tool actually calls documents.convert_document with what the
    model sent, rather than testing the mock in isolation from the wiring."""
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")

    seen = {}
    def fake_convert(path, fmt, timeout=60):
        seen["path"] = str(path)
        seen["fmt"] = fmt
        return f"Converted {path.name} to report.pdf (123 bytes)."
    monkeypatch.setattr(engine.documents, "convert_document", fake_convert)

    result = _convert(engine, src, "pdf")
    assert "Converted report.docx to report.pdf" in result
    assert seen["fmt"] == "pdf"
    assert seen["path"].endswith("report.docx")


@pytest.mark.parametrize("mode", ["readonly", "plan-only"])
def test_convert_document_is_gated_outside_full_auto(engine, tmp_path, mode, monkeypatch):
    monkeypatch.setattr(engine.documents, "convert_document",
                         lambda *a, **k: pytest.fail("must not run without the write gate"))
    engine.PERMISSION_MODE = mode
    engine.ALLOWED_PATHS = [tmp_path]
    src = tmp_path / "report.docx"
    src.write_bytes(b"x")
    out = src.with_suffix(".pdf")
    result = _convert(engine, src, "pdf")
    # plan-only denies with a different message than readonly's
    # ESCALATION_REQUEST format -- the real security property is "no file
    # written", mirroring test_create_document.py's lenient `or` form.
    assert result.startswith("ESCALATION_REQUEST\x1f") or not out.exists(), (
        "convert_document wrote a file without passing the write gate")


def test_convert_document_cannot_target_a_sensitive_path(engine, tmp_path, monkeypatch):
    """The credential floor is unconditional; a docx/pdf pair must not dodge
    it just because the tool's job is conversion, not creation."""
    monkeypatch.setattr(engine.documents, "convert_document",
                         lambda *a, **k: pytest.fail("must not reach documents.py for a sensitive path"))
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    target = tmp_path / "id_rsa"
    target.write_bytes(b"x")
    result = _convert(engine, target, "pdf")
    assert "Error" in result or "denied" in result.lower()
