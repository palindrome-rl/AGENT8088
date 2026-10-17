"""A plan lists a project's files by their short names; the project folder is named elsewhere in it. The files the
run wrote inside that folder are the files the plan asked for, and a scratch file the run removed, or a file it moved,
is not a deliverable that went missing."""
from pathlib import Path

from agent8088 import completion as c

REQUEST = ("Build the app in the proj/ folder. Create server.js and public/data/typechart.js for the backend, "
           "and a README.md that explains how to run it.")


def _write(path: Path, text="x = 1\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _missing(tmp_path, request, reply, written):
    return c.missing_outputs(request, reply, written, [tmp_path], [tmp_path / "artifacts"])


def test_files_written_inside_the_folder_the_plan_names_are_not_missing(tmp_path):
    written = [_write(tmp_path / "artifacts" / "proj" / "server.js"),
               _write(tmp_path / "artifacts" / "proj" / "public" / "data" / "typechart.js"),
               _write(tmp_path / "artifacts" / "proj" / "README.md")]
    assert _missing(tmp_path, REQUEST, "done", written) == []


def test_the_same_names_were_reported_missing_before(tmp_path):
    """The names are looked for at the top of the workspace, where nothing is."""
    named = c.named_output_paths(REQUEST)
    assert "server.js" in named and "public/data/typechart.js" in named


def test_a_file_that_was_never_written_is_still_missing(tmp_path):
    written = [_write(tmp_path / "artifacts" / "proj" / "server.js")]
    missing = _missing(tmp_path, REQUEST, "done", written)
    assert any("typechart.js" in m for m in missing) and any("README.md" in m for m in missing)
    assert not any(m.startswith("server.js") for m in missing)


def test_a_file_under_a_folder_the_request_does_not_name_does_not_count(tmp_path):
    written = [_write(tmp_path / "artifacts" / "elsewhere" / "server.js")]
    missing = _missing(tmp_path, REQUEST, "done", written)
    assert any(m.startswith("server.js") for m in missing)


def test_without_any_folder_named_the_top_level_path_is_still_required(tmp_path):
    written = [_write(tmp_path / "artifacts" / "proj" / "server.js")]
    missing = _missing(tmp_path, "Create server.js for the backend.", "done", written)
    assert any(m.startswith("server.js") for m in missing)


def test_a_scratch_file_the_run_removed_is_not_reported(tmp_path):
    gone = str(tmp_path / "artifacts" / "proj" / "_fix_spreads.js")
    kept = _write(tmp_path / "artifacts" / "proj" / "app.js")
    reply = "I used _fix_spreads.js to repair the data, then deleted it. app.js is complete."
    assert _missing(tmp_path, "make an app", reply, [gone, kept]) == []


def test_a_file_the_run_moved_is_not_reported(tmp_path):
    old = str(tmp_path / "proj" / "package.json")
    new = _write(tmp_path / "artifacts" / "proj" / "package.json", "{}")
    reply = "I moved package.json into artifacts/proj/."
    assert _missing(tmp_path, "make an app", reply, [old, new]) == []


def test_an_ordinary_file_that_went_missing_is_still_reported(tmp_path):
    gone = str(tmp_path / "artifacts" / "report.js")
    assert _missing(tmp_path, "make an app", "I wrote report.js for you", [gone]) == [gone]


def test_a_named_folder_with_nothing_written_in_it_is_still_missing(tmp_path):
    missing = _missing(tmp_path, REQUEST, "done", [])
    assert "proj/ (missing or empty)" in missing


def test_a_folder_of_that_name_somewhere_unrelated_does_not_count(tmp_path):
    written = [_write(tmp_path / "elsewhere" / "deep" / "proj" / "server.js")]
    missing = _missing(tmp_path, "Put the results in the proj/ folder.", "done", written)
    assert "proj/ (missing or empty)" in missing
