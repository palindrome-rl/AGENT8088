"""Missing-output hints locate artifacts without escaping the mirror folder."""
from pathlib import Path

from agent8088 import completion as C


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_absolute_folder_that_exists_in_artifacts_says_so(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    _write(artifacts / "demo-app" / "sprites" / "a.png")
    entry = f"{project / 'demo-app' / 'sprites'}/ (missing or empty)"
    out = C.annotate_missing([entry], [project], [artifacts])
    assert out == [entry.replace("(missing or empty)", "(missing or empty; but its content is at "
                                 f"{artifacts / 'demo-app' / 'sprites'})")]


def test_a_relative_folder_that_exists_in_artifacts_says_so(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    _write(artifacts / "out" / "r.txt")
    out = C.annotate_missing(["out/ (missing or empty)"], [project], [artifacts])
    assert out == [f"out/ (missing or empty; but its content is at {artifacts / 'out'})"]


def test_a_missing_file_that_exists_in_artifacts_says_so(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    _write(artifacts / "server.js", "listen()")
    out = C.annotate_missing([str(project / "server.js")], [project], [artifacts])
    assert out == [f"{project / 'server.js'} (its content is at {artifacts / 'server.js'})"]


def test_nothing_is_added_when_the_content_is_nowhere(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    artifacts.mkdir(parents=True)
    entries = ["out/ (missing or empty)", str(project / "server.js")]
    assert C.annotate_missing(entries, [project], [artifacts]) == entries


def test_an_empty_folder_in_artifacts_does_not_count(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    (artifacts / "out").mkdir(parents=True)
    assert C.annotate_missing(["out/ (missing or empty)"], [project], [artifacts]) == ["out/ (missing or empty)"]


def test_entries_that_already_say_where_are_left_alone(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    _write(artifacts / "a.js")
    said = f"a.js (found only in {artifacts}; create it at the requested path)"
    assert C.annotate_missing([said], [project], [artifacts]) == [said]


def test_a_path_outside_every_root_is_left_alone(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    _write(artifacts / "x" / "y.txt")
    other = str(tmp_path / "somewhere" / "x")
    entry = f"{other}/ (missing or empty)"
    assert C.annotate_missing([entry], [project], [artifacts]) == [entry]


def test_the_engine_wrapper_uses_it(tmp_path, monkeypatch):
    from agent8088 import engine
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    _write(artifacts / "app" / "index.html", "<html></html>")

    class Trajectory:
        start_files = {}
        data = {}

        def written_paths(self):
            return []

    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    entry = f"{project / 'app'}/ (missing or empty)"
    monkeypatch.setattr(engine.completion_checks, "missing_outputs", lambda *a, **k: [entry])
    monkeypatch.chdir(tmp_path)
    missing = engine._missing_required_files(Trajectory(), "done", "anything")
    assert missing == [entry.replace("(missing or empty)",
                                     f"(missing or empty; but its content is at {artifacts / 'app'})")], missing


def test_a_relative_parent_escape_is_not_artifacts_content(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    artifacts.mkdir(parents=True)
    _write(project / "outside.txt", "existing input, not an artifact")
    entry = "../outside.txt"
    assert C.annotate_missing([entry], [project], [artifacts]) == [entry]


def test_a_legal_dot_prefixed_folder_is_not_confused_with_parent_traversal(tmp_path):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    _write(artifacts / "..cache" / "out.txt")
    entry = f"{project / '..cache'}/ (missing or empty)"
    assert "its content is at" in C.annotate_missing([entry], [project], [artifacts])[0]
