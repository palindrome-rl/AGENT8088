"""write_file's content source is content OR source_url — never neither.

tools.txt marks both `content` and `source_url` optional so that either can
carry the bytes. That makes a call supplying neither schema-valid, and the old
implementation fell through to writing "", so a model that dropped its content
key silently truncated an existing file to zero bytes. These tests pin the
distinction the fix rests on: an *absent* content key has nothing to write and
must be refused, while an *explicit* empty string is a real request to empty the
file and must still go through.
"""
import json

import pytest


@pytest.fixture
def ready(engine, tmp_path):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    return engine


def _write(engine, **args):
    return engine.exec_tool("write_file", json.dumps(args))


# --- the defect ------------------------------------------------------------
def test_no_content_and_no_source_url_leaves_an_existing_file_untouched(ready, tmp_path):
    target = tmp_path / "has-content.txt"
    target.write_text("important data", encoding="utf-8")

    result = _write(ready, filename=str(target))

    assert target.read_text(encoding="utf-8") == "important data"
    assert "Wrote 0 bytes" not in result


def test_the_refusal_names_both_ways_to_supply_content(ready, tmp_path):
    target = tmp_path / "has-content.txt"
    target.write_text("important data", encoding="utf-8")

    result = _write(ready, filename=str(target))

    assert result.startswith("Error:")
    assert "content" in result and "source_url" in result


def test_a_contentless_call_does_not_create_a_new_file(ready, tmp_path):
    target = tmp_path / "brand-new.txt"

    result = _write(ready, filename=str(target))

    assert not target.exists()
    assert result.startswith("Error:")


def test_a_refused_call_does_not_spend_the_turn_write_budget(ready, tmp_path):
    """A write that never happened must not lock out the real one."""
    target = tmp_path / "has-content.txt"
    target.write_text("important data", encoding="utf-8")
    ready._turn_writes = 0

    _write(ready, filename=str(target))

    assert ready._turn_writes == 0


# --- what must keep working ------------------------------------------------
def test_an_explicit_empty_string_still_truncates(ready, tmp_path):
    """Emptying a file on purpose is a legitimate request."""
    target = tmp_path / "wipe-me.txt"
    target.write_text("goodbye", encoding="utf-8")

    result = _write(ready, filename=str(target), content="")

    assert target.read_text(encoding="utf-8") == ""
    assert "Wrote 0 bytes" in result


def test_an_ordinary_write_is_unaffected(ready, tmp_path):
    target = tmp_path / "normal.txt"

    result = _write(ready, filename=str(target), content="hello world")

    assert target.read_text(encoding="utf-8") == "hello world"
    assert "Wrote 11 bytes" in result


def test_edit_file_still_deletes_a_match_with_an_empty_new_string(ready, tmp_path):
    """edit_file's content_arg is new_string, and empty means 'delete the match'."""
    target = tmp_path / "edit-me.txt"
    target.write_text("alpha beta gamma", encoding="utf-8")

    ready.exec_tool("edit_file", json.dumps({
        "filename": str(target), "old_string": "beta ", "new_string": ""}))

    assert target.read_text(encoding="utf-8") == "alpha gamma"


def test_convert_document_carries_no_content_argument_at_all(ready, tmp_path):
    """It declares write_text but names no content_arg — the guard must not eat it."""
    missing = tmp_path / "nope.docx"

    result = ready.exec_tool("convert_document", json.dumps(
        {"filename": str(missing), "format": "pdf"}))

    # Whatever it says, it must not be the contentless-write refusal.
    assert "needs content or source_url" not in result
