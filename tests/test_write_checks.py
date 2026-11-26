"""Cheap checks after every write: a broken file is found on the next turn, not at the end of the run.

After write_file or edit_file lands a file, a parse that runs nothing looks at it: Python and JSON are parsed, a
JavaScript file goes through `node --check`, and an HTML page's local src/href files must be there. A problem is
added to the tool result in one line and kept in the ledger until the file is written cleanly again. Appends are
not checked (a file written in parts is incomplete until its last part), and neither is a large file.
"""
import shutil
from pathlib import Path

import pytest

from agent8088 import write_checks as W
from agent8088.trajectory import TrajectoryState


def test_valid_files_have_no_problem(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "a.json").write_text('{"x": [1, 2]}')
    (tmp_path / "logo.png").write_bytes(b"png")
    (tmp_path / "app.js").write_text("export const x = 1;\n")
    (tmp_path / "index.html").write_text('<script src="app.js"></script><img src="logo.png">'
                                         '<a href="https://example.com/x.html">x</a><a href="#top">t</a>')
    for name in ("a.py", "a.json", "index.html"):
        assert W.check_file(tmp_path / name, node=None) == "", name


def test_python_syntax_errors_name_the_line(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("def f(:\n    return 1\n")
    assert W.check_file(path, node=None).startswith("line 1:")


def test_json_errors_name_the_line(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{\n  "a": 1,\n  "b": \n}')
    assert "line 4" in W.check_file(path, node=None)


def test_a_page_that_points_at_missing_local_files(tmp_path):
    path = tmp_path / "index.html"
    path.write_text('<link rel="stylesheet" href="css/site.css"><script src="js/game.js?v=2"></script>'
                    '<img src="data:image/png;base64,AAA"><a href="mailto:x@y.z">m</a>')
    problem = W.check_file(path, node=None)
    assert "css/site.css" in problem and "js/game.js" in problem and "data:" not in problem


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed here")
def test_javascript_syntax_errors_are_found_without_running_anything(tmp_path):
    marker = tmp_path / "ran.txt"
    path = tmp_path / "app.js"
    path.write_text(f"require('fs').writeFileSync({str(marker)!r}, 'x');\nfunction (\n")
    problem = W.check_file(path, node=shutil.which("node"))
    assert problem and "SyntaxError" in problem
    assert not marker.exists()
    path.write_text(f"require('fs').writeFileSync({str(marker)!r}, 'x');\n")
    assert W.check_file(path, node=shutil.which("node")) == ""
    assert not marker.exists()   # --check parses only


def test_large_and_unknown_files_are_skipped(tmp_path):
    big = tmp_path / "big.json"
    big.write_text("{" * (W.MAX_BYTES + 10))
    assert W.check_file(big, node=None) == ""
    (tmp_path / "notes.txt").write_text("def f(:")
    assert W.check_file(tmp_path / "notes.txt", node=None) == ""


def test_the_ledger_keeps_a_problem_until_the_file_is_written_cleanly(tmp_path):
    state = TrajectoryState({}, goal="build it", message_count=1, workspace=str(tmp_path))
    op = state.before_tool("write_file")
    state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(tmp_path / "a.py"))
    state.note_write_problem(str(tmp_path / "a.py"), "line 1: invalid syntax")
    assert "Problems in written files: a.py (line 1: invalid syntax)" in state.state_note()
    assert state.has_open_attention()
    before = state.state_signature()
    state.note_write_problem(str(tmp_path / "a.py"), "")
    assert "Problems in written files" not in state.state_note()
    assert state.state_signature() != before


@pytest.fixture
def project(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    return tmp_path


def _state(tmp_path):
    return TrajectoryState({}, goal="build it", message_count=1, workspace=str(tmp_path))


def test_the_engine_adds_a_one_line_note_after_a_broken_write(engine, project):
    (project / "a.py").write_text("x = (\n")
    state = _state(project)
    note = engine._write_check_note("edit_file", {"filename": str(project / "a.py")}, True, state)
    assert note.startswith("\n[check: a.py") and "line" in note
    assert state.data["write_problems"][str((project / "a.py").resolve())].startswith("line 1:")


def test_appends_and_reads_are_not_checked(engine, project):
    (project / "a.py").write_text("x = (\n")
    state = _state(project)
    assert engine._write_check_note("write_file", {"filename": "a.py", "mode": "append"}, True, state) == ""
    assert engine._write_check_note("read_text", {"filename": "a.py"}, False, state) == ""
    assert engine._write_check_note("edit_file", {"filename": "a.py"}, False, state) == ""   # nothing landed


def test_a_clean_rewrite_clears_the_problem(engine, project):
    state = _state(project)
    (project / "a.json").write_text("{")
    engine._write_check_note("write_file", {"filename": str(project / "a.json")}, True, state)
    assert state.data["write_problems"]
    (project / "a.json").write_text("{}")
    assert engine._write_check_note("write_file", {"filename": str(project / "a.json")}, True, state) == ""
    assert not state.data["write_problems"]
