"""copy_file / move_file — filing an existing binary file where the user asked.

They declare mode=write_text, so the destination passes every write guard. The
bug they fix: a PDF saved into artifacts/ could not reach Drive/Finance/ because
write_file only writes text or fetches a URL, and a new folder was diverted back
into artifacts/.
"""
import json

import pytest

PDF = b"%PDF-1.4\x00\xff\xfe binary \x80\x81 tail\r\n\x00"


def _call(engine, name, source, destination):
    return engine.exec_tool(name, json.dumps({"source": str(source), "destination": str(destination)}))


@pytest.fixture
def ready(engine, tmp_path, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    engine.ALLOWED_PATHS = [tmp_path]
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    (tmp_path / "artifacts").mkdir()
    src = tmp_path / "artifacts" / "invoice.pdf"
    src.write_bytes(PDF)
    engine._CURRENT_RUN_MESSAGES = [{"role": "user", "content": "put the invoice in Drive/Finance/ and Drive/inv-2026.pdf"}]
    return engine, tmp_path, src


def test_registered_with_the_write_mode(engine):
    for name in ("copy_file", "move_file"):
        assert engine.TOOL_SPECS[name]["mode"] == "write_text"


def test_copy_into_a_new_project_folder_is_byte_exact(ready):
    engine, root, src = ready
    out = _call(engine, "copy_file", src, "Drive/Finance/")
    dest = root / "Drive" / "Finance" / "invoice.pdf"
    assert dest.read_bytes() == PDF, out
    assert src.read_bytes() == PDF  # copy leaves the original


def test_move_removes_the_original(ready):
    engine, root, src = ready
    _call(engine, "move_file", src, "Drive/Finance/inv-2026.pdf")
    assert (root / "Drive" / "Finance" / "inv-2026.pdf").read_bytes() == PDF
    assert not src.exists()


def test_existing_destination_is_replaced_after_a_backup(ready):
    engine, root, src = ready
    (root / "Drive").mkdir()
    (root / "Drive" / "invoice.pdf").write_bytes(b"old")
    _call(engine, "copy_file", src, "Drive/invoice.pdf")
    assert (root / "Drive" / "invoice.pdf").read_bytes() == PDF


def test_missing_source_is_refused(ready):
    engine, root, _ = ready
    out = _call(engine, "copy_file", root / "nope.pdf", "Drive/")
    assert out.startswith("Error") and not (root / "Drive").exists()


def test_sensitive_destination_is_refused(ready):
    engine, root, src = ready
    out = _call(engine, "copy_file", src, root / ".env")
    assert out.startswith("Error") and not (root / ".env").exists()


def test_destination_outside_allowed_paths_is_refused(ready, tmp_path_factory):
    engine, _, src = ready
    outside = tmp_path_factory.mktemp("outside") / "x.pdf"
    assert _call(engine, "copy_file", src, outside).startswith("Error")
    assert not outside.exists()


def test_move_only_takes_files_from_inside_the_project(ready, tmp_path_factory):
    engine, root, _ = ready
    other = tmp_path_factory.mktemp("elsewhere") / "a.pdf"
    other.write_bytes(PDF)
    engine.ALLOWED_PATHS = [root, other.parent]
    out = _call(engine, "move_file", other, "Drive/a.pdf")
    assert out.startswith("Error") and other.exists()


def test_readonly_mode_blocks_it_like_any_write(ready):
    engine, root, src = ready
    engine.PERMISSION_MODE = "readonly"
    _call(engine, "copy_file", src, "Drive/")
    assert not (root / "Drive" / "invoice.pdf").exists()


def test_a_folder_nobody_named_is_not_invented_in_the_project(ready):
    """No existing folder and the user never said it: stays in artifacts/ and says so."""
    engine, root, src = ready
    engine._CURRENT_RUN_MESSAGES = [{"role": "user", "content": "file the invoice somewhere sensible"}]
    out = _call(engine, "copy_file", src, "Made/Up/Dir/")
    assert not (root / "Made").exists()
    assert (root / "artifacts" / "Made" / "Up" / "Dir" / "invoice.pdf").read_bytes() == PDF
    assert "NOT" in out and "artifacts/" in out


def test_a_folder_the_user_named_is_created(ready):
    engine, root, src = ready
    engine._CURRENT_RUN_MESSAGES = [{"role": "user", "content": "save the attached invoice into Drive/Finance"}]
    _call(engine, "copy_file", src, "Drive/Finance/")
    assert (root / "Drive" / "Finance" / "invoice.pdf").read_bytes() == PDF
