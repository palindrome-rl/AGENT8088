"""A file written to an absolute path inside the project is stored under artifacts/; reading that path back must find it.

Seen in a demo run: the agent wrote E:\\game store 8088\\js\\skifree\\state.js, the write went to artifacts\\js\\skifree\\,
and read_text of the same path answered "No such file". Writes redirected an absolute path, reads did not."""
import pytest


@pytest.fixture
def roots(engine, monkeypatch, tmp_path):
    project = (tmp_path / "game store").resolve()
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [str(project)])
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "_PROJECT_FOLDER_ROOT", {}, raising=False)
    return project, artifacts


def test_an_absolute_path_that_was_written_to_artifacts_is_read_from_there(engine, roots):
    project, artifacts = roots
    asked = project / "js" / "skifree" / "state.js"
    written = engine.resolve_write_path(str(asked))
    assert written == artifacts / "js" / "skifree" / "state.js"
    written.parent.mkdir(parents=True)
    written.write_text("export const x = 1;\n", encoding="utf-8")
    assert engine.resolve_user_path(str(asked)) == written


def test_a_file_that_exists_where_it_was_named_wins(engine, roots):
    project, artifacts = roots
    (project / "js").mkdir()
    (project / "js" / "a.js").write_text("project", encoding="utf-8")
    (artifacts / "js").mkdir()
    (artifacts / "js" / "a.js").write_text("artifacts", encoding="utf-8")
    assert engine.resolve_user_path(str(project / "js" / "a.js")) == project / "js" / "a.js"


def test_a_missing_file_stays_missing_at_the_stated_path(engine, roots):
    project, _ = roots
    asked = project / "js" / "nothing.js"
    assert engine.resolve_user_path(str(asked)) == asked


def test_a_path_already_under_artifacts_is_unchanged(engine, roots):
    _, artifacts = roots
    inside = artifacts / "x" / "y.js"
    assert engine.resolve_user_path(str(inside)) == inside


def test_a_path_outside_the_project_is_still_refused(engine, roots, tmp_path):
    with pytest.raises(ValueError):
        engine.resolve_user_path(str(tmp_path.parent / "elsewhere" / "x.txt"))


def test_relative_paths_still_fall_back(engine, roots):
    _, artifacts = roots
    (artifacts / "js").mkdir()
    (artifacts / "js" / "b.js").write_text("b", encoding="utf-8")
    assert engine.resolve_user_path("js/b.js") == artifacts / "js" / "b.js"


def test_nothing_is_redirected_in_a_task_container(engine, roots, monkeypatch):
    project, artifacts = roots
    (artifacts / "js").mkdir()
    (artifacts / "js" / "c.js").write_text("c", encoding="utf-8")
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    assert engine.resolve_user_path(str(project / "js" / "c.js")) == project / "js" / "c.js"


def test_the_write_result_says_when_an_absolute_path_was_redirected(engine, roots):
    project, artifacts = roots
    asked = project / "js" / "d.js"
    note = engine._redirected_note(str(asked), engine.resolve_write_path(str(asked)))
    assert str(asked) in note and str(artifacts / "js" / "d.js") in note and "artifacts" in note


@pytest.mark.parametrize("name", ["d.js", "js/d.js"])
def test_no_note_for_relative_or_unredirected_paths(engine, roots, name):
    project, artifacts = roots
    assert engine._redirected_note(name, engine.resolve_write_path(name)) == ""
    inside = artifacts / "e.js"
    assert engine._redirected_note(str(inside), inside) == ""
