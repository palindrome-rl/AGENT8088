"""create_document — the deterministic fallback for building Office files.

It declares mode=write_text rather than a mode of its own. That is the point of
these tests: it must be gated by exactly the same layers as write_file, because
a dozen sites key on "write_text" and a private mode would have to be added to
every one of them. If any of the permission tests below start passing when they
should deny, the tool has escaped the write gate.
"""
import json

import pytest


def _create(engine, path, content):
    return engine.exec_tool(
        "create_document", json.dumps({"filename": str(path), "content": content}))


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


def _read_back(engine, path):
    return engine.exec_tool("read_text", json.dumps({"filename": str(path)}))


# --- it is registered like every other tool --------------------------------
def test_create_document_is_registered_with_the_write_mode(engine):
    assert engine.TOOL_SPECS["create_document"]["mode"] == "write_text"


def test_its_description_survived_the_pipe_delimited_registry(engine):
    """tools.txt splits on '|', so a description containing one gets truncated."""
    desc = engine.TOOL_SPECS["create_document"]["description"]
    assert "reportlab" in desc, "description was cut short by a stray pipe"


# --- round trip: build it, then read it back through the reader -------------
def test_docx_round_trips_through_read_text(ready, tmp_path):
    out = tmp_path / "report.docx"
    result = _create(ready, out, "# Q4 Report\nRevenue grew.\n| Region | Total |\n| EU | 12 |")
    assert "Created" in result
    assert out.exists()

    back = _read_back(ready, out)
    assert "Q4 Report" in back
    assert "Revenue grew." in back
    assert "| Region | Total |" in back


def test_xlsx_round_trips_and_keeps_numbers_numeric(ready, tmp_path):
    out = tmp_path / "data.xlsx"
    _create(ready, out, "## Sheet: Sales\n| Region | Total |\n| EU | 12 |")
    assert out.exists()

    import openpyxl
    wb = openpyxl.load_workbook(out)
    # The "## Sheet:" line names the sheet, it does not occupy a row, so the
    # header row is 1 and the first data row is 2.
    assert wb["Sales"]["A1"].value == "Region"
    assert wb["Sales"]["B2"].value == 12  # the int, not the string "12"
    wb.close()


def test_consecutive_rows_become_one_table_not_many(ready, tmp_path):
    out = tmp_path / "t.docx"
    _create(ready, out, "| a | b |\n| c | d |\n| e | f |")
    import docx
    assert len(docx.Document(str(out)).tables) == 1


# --- honest refusals -------------------------------------------------------
def test_pdf_is_refused_with_a_usable_alternative(ready, tmp_path):
    result = _create(ready, tmp_path / "out.pdf", "# Hello")
    assert "reportlab" in result
    assert not (tmp_path / "out.pdf").exists()


def test_pptx_without_a_slide_line_says_so_instead_of_writing_an_empty_deck(ready, tmp_path):
    out = tmp_path / "deck.pptx"
    result = _create(ready, out, "just a loose line with no slide header")
    assert "at least one" in result
    assert not out.exists()


# --- the write gate, in every permission mode ------------------------------
@pytest.mark.parametrize("mode", ["readonly", "plan-only"])
def test_create_document_is_gated_outside_full_auto(engine, tmp_path, mode):
    engine.PERMISSION_MODE = mode
    engine.ALLOWED_PATHS = [tmp_path]
    out = tmp_path / "sneaky.docx"
    result = _create(engine, out, "# Should not be written")
    assert result.startswith("ESCALATION_REQUEST\x1f") or not out.exists(), (
        "create_document wrote a file without passing the write gate")


def test_create_document_cannot_write_a_sensitive_path(engine, tmp_path):
    """The credential floor is unconditional; a .docx extension must not dodge it."""
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    target = tmp_path / "id_rsa"
    result = _create(engine, target, "# secrets")
    assert not target.exists(), f"sensitive path was written: {result}"
