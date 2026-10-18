"""One project folder lives in one root.

A file the person typed in their message stays at the project root, and a file the agent invents goes to artifacts/. A
plan that named `pokemon-doubles/package.json` but not `pokemon-doubles/server.js` therefore got a project split over two
folders (`workspace/pokemon-doubles` and `workspace/artifacts/pokemon-doubles`): the page could not find its stylesheet and
the server could not start. The first write into a folder now decides, and the rest of it follows."""
import pytest


@pytest.fixture
def roots(engine, tmp_path, monkeypatch):
    project = tmp_path / "workspace"
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [project])
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "_PROJECT_FOLDER_ROOT", {})
    monkeypatch.setattr(engine, "_CURRENT_RUN_MESSAGES",
                        [{"role": "user", "content": "Build it in pokemon-doubles/. Files: pokemon-doubles/package.json "
                                                     "and pokemon-doubles/public/index.html."}])
    return project, artifacts


def _write(engine, raw):
    path = engine.resolve_write_path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def test_a_named_file_first_keeps_the_whole_folder_at_the_project_root(engine, roots):
    project, artifacts = roots
    first = _write(engine, "pokemon-doubles/package.json")
    second = _write(engine, "pokemon-doubles/server.js")
    third = _write(engine, "pokemon-doubles/public/style.css")
    assert first.parent == second.parent == project / "pokemon-doubles"
    assert third == project / "pokemon-doubles" / "public" / "style.css"
    assert not (artifacts / "pokemon-doubles").exists()


def test_an_invented_file_first_keeps_the_whole_folder_in_artifacts(engine, roots):
    project, artifacts = roots
    first = _write(engine, "pokemon-doubles/server.js")
    second = _write(engine, "pokemon-doubles/package.json")        # named by the person, but the folder is already there
    assert first.parent == second.parent == artifacts / "pokemon-doubles"
    assert not (project / "pokemon-doubles").exists()


def test_another_folder_is_decided_on_its_own(engine, roots):
    project, artifacts = roots
    _write(engine, "pokemon-doubles/package.json")
    other = _write(engine, "notes-tool/main.py")
    assert other == artifacts / "notes-tool" / "main.py"


def test_a_folder_the_person_deleted_is_decided_again(engine, roots):
    import shutil
    project, artifacts = roots
    _write(engine, "pokemon-doubles/package.json")
    shutil.rmtree(project / "pokemon-doubles")
    again = _write(engine, "pokemon-doubles/server.js")
    assert again == artifacts / "pokemon-doubles" / "server.js"
    # The replacement decision must also hold for the NEXT named file.
    assert _write(engine, "pokemon-doubles/package.json").parent == artifacts / "pokemon-doubles"


def test_a_bare_file_and_an_absolute_path_are_not_affected(engine, roots):
    project, artifacts = roots
    _write(engine, "pokemon-doubles/package.json")
    assert engine.resolve_write_path("notes.txt") == artifacts / "notes.txt"
    target = project / "pokemon-doubles" / "later.js"
    assert engine.resolve_write_path(str(target)) == target


def test_the_decision_is_kept_per_project_root(engine, roots, tmp_path, monkeypatch):
    _write(engine, "pokemon-doubles/package.json")
    other = tmp_path / "other"
    (other / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", other)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", other / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [other])
    assert engine.resolve_write_path("pokemon-doubles/server.js") == other / "artifacts" / "pokemon-doubles" / "server.js"
