"""A shell "No such file" names the copy the file tools see, so a path that works for read_file and fails in the
shell (or the other way round) no longer reads as "the file does not exist"."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def roots(engine, tmp_path, monkeypatch):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [project])
    monkeypatch.setattr(engine.capabilities, "get", lambda name: SimpleNamespace(active="native"))
    return project, artifacts


MISSING = "python: can't open file 'src/app.py': [Errno 2] No such file or directory"


def test_a_project_file_the_shell_cannot_see_gets_its_full_path(engine, roots):
    project, artifacts = roots
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("print(1)")
    note = engine._shell_missing_file_note(MISSING, "python src/app.py", artifacts)
    assert f"not in the shell's folder {artifacts.resolve()}" in note
    assert str((project / "src" / "app.py").resolve()) in note
    assert "use that full path" in note


def test_a_data_file_named_only_in_the_error_is_found_too(engine, roots):
    project, artifacts = roots
    (project / "data.csv").write_text("a,b")
    error = "FileNotFoundError: [Errno 2] No such file or directory: 'data.csv'"
    assert str((project / "data.csv").resolve()) in engine._shell_missing_file_note(error, "python load.py", artifacts)


def test_an_artifacts_file_is_named_when_the_shell_runs_in_the_project(engine, roots):
    project, artifacts = roots
    (artifacts / "fib.py").write_text("")
    note = engine._shell_missing_file_note("No such file or directory", "python ./fib.py", project)
    assert str((artifacts / "fib.py").resolve()) in note


def test_docker_is_told_to_copy_the_file_in(engine, roots, monkeypatch):
    project, artifacts = roots
    (project / "notes.txt").write_text("")
    monkeypatch.setattr(engine.capabilities, "get", lambda name: SimpleNamespace(active="docker"))
    assert "copy_file" in engine._shell_missing_file_note("cat: notes.txt: No such file or directory",
                                                          "cat notes.txt", artifacts)


def test_nothing_is_added_without_a_not_found_error_or_another_copy(engine, roots):
    project, artifacts = roots
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("")
    assert engine._shell_missing_file_note("ok", "python src/app.py", artifacts) == ""
    assert engine._shell_missing_file_note(MISSING, "python other/app.py", artifacts) == ""
    (artifacts / "src").mkdir()
    (artifacts / "src" / "app.py").write_text("")   # the shell's own copy exists: its error is about something else
    assert engine._shell_missing_file_note(MISSING, "python src/app.py", artifacts) == ""


def test_an_existing_file_outside_allowed_paths_is_not_disclosed(engine, roots, monkeypatch):
    project, artifacts = roots
    (project / "private.txt").write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [artifacts])
    assert engine._shell_missing_file_note("No such file or directory", "cat private.txt", artifacts) == ""
