"""A bare filename read back finds the copy the agent just wrote.

Seen in a live run: the agent wrote `index.html`, which went to artifacts/ as
every bare-name write does, then read `index.html` back. The project folder
held an older `index.html`, and reads of a bare name preferred the project copy
whenever one existed, so the agent saw its edit "not take effect" and spent
several turns diagnosing a stale cache that did not exist.

When both copies exist, a bare-name read now takes the newer one and says that
the other copy exists, so neither side is silent.
"""
import os
import time

import pytest


@pytest.fixture
def roots(engine, monkeypatch, tmp_path):
    project = tmp_path / "project"
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project.resolve())
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts.resolve())
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [str(project.resolve())])
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "_PROJECT_FOLDER_ROOT", {}, raising=False)
    return project.resolve(), artifacts.resolve()


def _write(path, text, age):
    path.write_text(text, encoding="utf-8")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def test_the_copy_just_written_wins_over_an_older_project_file(engine, roots):
    project, artifacts = roots
    _write(project / "index.html", "old", age=3600)
    written = engine.resolve_write_path("index.html")
    assert written == artifacts / "index.html"
    _write(written, "new", age=0)
    assert engine.resolve_user_path("index.html") == written


def test_a_project_file_edited_after_the_artifact_wins(engine, roots):
    project, artifacts = roots
    _write(artifacts / "notes.txt", "agent", age=3600)
    _write(project / "notes.txt", "user", age=0)
    assert engine.resolve_user_path("notes.txt") == project / "notes.txt"


def test_a_single_copy_is_read_wherever_it_is(engine, roots):
    project, artifacts = roots
    _write(project / "README.md", "readme", age=10)
    _write(artifacts / "out.json", "{}", age=10)
    assert engine.resolve_user_path("README.md") == project / "README.md"
    assert engine.resolve_user_path("out.json") == artifacts / "out.json"


def test_paths_with_folders_keep_their_rule(engine, roots):
    project, artifacts = roots
    (project / "js").mkdir()
    (artifacts / "js").mkdir()
    _write(project / "js" / "a.js", "project", age=3600)
    _write(artifacts / "js" / "a.js", "artifacts", age=0)
    assert engine.resolve_user_path("js/a.js") == project / "js" / "a.js"


def test_the_read_names_the_other_copy(engine, roots, monkeypatch):
    project, artifacts = roots
    _write(project / "index.html", "old", age=3600)
    _write(artifacts / "index.html", "new page", age=0)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    result = engine.run_tool("read_text", {"path": "index.html"})
    assert "new page" in result
    assert str(project / "index.html") in result
    assert "older" in result


def test_no_note_when_there_is_one_copy(engine, roots, monkeypatch):
    _, artifacts = roots
    _write(artifacts / "only.txt", "alone", age=0)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    result = engine.run_tool("read_text", {"path": "only.txt"})
    assert "alone" in result and "also exists" not in result
