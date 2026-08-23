"""read_text extracting .docx/.xlsx/.pptx/.pdf, and the guards around it.

Document extraction deliberately lives inside the existing read_text mode
rather than in a tool of its own, so the sensitive-file floor, the read path
zones and check_permission() all still apply with no second gate to keep in
sync. The floor tests below are what prove that: a .docx named like a
credential must be refused in every permission mode, exactly as a .txt is.
"""
import json
import zipfile

import pytest

DOC_XML = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    '<w:p><w:r><w:t>Quarterly summary</w:t></w:r></w:p>'
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Region</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>Revenue</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    "</w:body></w:document>"
)


def _docx(path, body=DOC_XML):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", body)
    return path


def _read(engine, path, **extra):
    args = {"filename": str(path)}
    args.update(extra)
    return engine.exec_tool("read_text", json.dumps(args))


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


# --- extraction ------------------------------------------------------------
def test_docx_paragraphs_and_tables_are_extracted(ready, tmp_path):
    result = _read(ready, _docx(tmp_path / "report.docx"))
    assert "Quarterly summary" in result
    assert "| Region | Revenue |" in result


def test_docx_is_no_longer_a_unicode_crash(ready, tmp_path):
    """The whole point: this used to raise an uncaught UnicodeDecodeError."""
    result = _read(ready, _docx(tmp_path / "report.docx"))
    assert "Traceback" not in result and "UnicodeDecodeError" not in result


def test_corrupt_docx_reports_instead_of_raising(ready, tmp_path):
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"this is not a zip file at all")
    assert "not a valid .docx" in _read(ready, bad)


def test_plain_text_files_are_untouched_by_the_document_path(ready, tmp_path):
    plain = tmp_path / "notes.txt"
    plain.write_text("hello world", encoding="utf-8")
    assert _read(ready, plain) == "hello world"


# --- the binary root fix ---------------------------------------------------
def test_binary_file_raises_valueerror_not_unicodedecodeerror(engine, tmp_path):
    blob = tmp_path / "image.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\xfd")
    with pytest.raises(ValueError, match="Not a text file"):
        engine._read_text_limited(blob)


def test_writing_over_a_binary_file_still_works(ready, tmp_path):
    """The write path snapshots old content for its diff; that read must not
    crash the write just because the existing bytes are not UTF-8."""
    target = tmp_path / "image.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    result = ready.exec_tool(
        "write_file", json.dumps({"filename": str(target), "content": "now text"}))
    assert "Wrote" in result
    assert target.read_text(encoding="utf-8") == "now text"


# --- pagination ------------------------------------------------------------
def test_short_files_get_no_pagination_header(ready, tmp_path):
    # newline="" so the fixture is byte-identical on Windows, where write_text
    # would otherwise turn \n into \r\n and this would compare unequal for a
    # reason that has nothing to do with pagination.
    short = tmp_path / "short.txt"
    short.write_text("one\ntwo\nthree", encoding="utf-8", newline="")
    assert _read(ready, short) == "one\ntwo\nthree"


def test_long_file_reports_its_true_length(ready, tmp_path):
    long_file = tmp_path / "long.txt"
    long_file.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    result = _read(ready, long_file)
    assert "of 500" in result
    assert "line 0" in result
    assert "line 499" not in result  # the tail is genuinely withheld, not hidden


def test_offset_reads_on_from_where_the_last_page_stopped(ready, tmp_path):
    long_file = tmp_path / "long.txt"
    long_file.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    result = _read(ready, long_file, offset=200, limit=10)
    assert "line 200" in result
    assert "line 199" not in result


def test_offset_past_the_end_says_so(ready, tmp_path):
    f = tmp_path / "small.txt"
    f.write_text("a\nb\nc", encoding="utf-8")
    assert "past the end" in _read(ready, f, offset=999)


def test_garbage_offset_falls_back_instead_of_crashing(ready, tmp_path):
    f = tmp_path / "small.txt"
    f.write_text("a\nb\nc", encoding="utf-8", newline="")
    assert _read(ready, f, offset="not-a-number") == "a\nb\nc"


# --- size guard ------------------------------------------------------------
def test_oversized_document_is_refused(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(ready, "MAX_DOCUMENT_BYTES", 100)
    big = _docx(tmp_path / "big.docx", DOC_XML * 200)
    assert "too large" in _read(ready, big)


# --- the security floor, in every permission mode --------------------------
@pytest.mark.parametrize("mode", ["readonly", "full-auto", "plan-only"])
def test_sensitive_document_is_refused_in_every_permission_mode(engine, tmp_path, mode):
    """A .docx must not become a way around the credential floor. This is the
    always-on layer CLAUDE.md requires proving in all three modes."""
    engine.PERMISSION_MODE = mode
    engine.ALLOWED_PATHS = [tmp_path]
    secret = _docx(tmp_path / "id_rsa.docx")
    result = _read(engine, secret)
    assert "Quarterly summary" not in result
