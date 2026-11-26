"""A problem the check after a write found is checked again before it decides the verdict.

Seen in two runs: the model wrote index.html first and the files it points at afterwards. The check right after the
first write found style.css and game.js missing and kept that, although both were written next, every check passed
and the page played. The leftover entry made the verdict NOT VERIFIED ("points at local files that are not there").
"""
import pytest

from agent8088.trajectory import TrajectoryState

PAGE = '<html><head><link rel="stylesheet" href="style.css"></head><body><script src="game.js"></script></body></html>'


@pytest.fixture
def project(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    return tmp_path


def _write(engine, state, path, text):
    path.write_text(text, encoding="utf-8")
    op = state.before_tool("write_file")
    state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(path))
    return engine._write_check_note("write_file", {"filename": str(path)}, True, state)


def _tested(state):
    op = state.before_tool("run_tests")
    state.after_tool(op, "3 passed", failed=False, blocked=False, mutated=False, verdict="passed", command="pytest -q")
    state.mark_subgoals([])


def test_writing_the_missing_files_clears_the_page_problem(engine, project):
    state = TrajectoryState({}, goal="Build a small game page.", message_count=1, workspace=str(project))
    assert "not there" in _write(engine, state, project / "index.html", PAGE)
    _write(engine, state, project / "style.css", "body { margin: 0; }")
    _write(engine, state, project / "game.js", "console.log('ok');")
    assert state.write_problems() == {}
    _tested(state)
    assert engine._verdict(state, "Done.")[0] != "NOT VERIFIED"


def test_files_made_another_way_are_seen_at_the_verdict(engine, project):
    state = TrajectoryState({}, goal="Build a small game page.", message_count=1, workspace=str(project))
    _write(engine, state, project / "index.html", PAGE)
    (project / "style.css").write_text("body {}", encoding="utf-8")   # written by a shell command, say
    (project / "game.js").write_text("1;", encoding="utf-8")
    _tested(state)
    assert engine._verdict(state, "Done.")[0] != "NOT VERIFIED"
    assert state.write_problems() == {}


def test_a_file_that_is_never_written_keeps_it(engine, project):
    state = TrajectoryState({}, goal="Build a small game page.", message_count=1, workspace=str(project))
    _write(engine, state, project / "index.html", PAGE)
    _write(engine, state, project / "style.css", "body {}")
    _tested(state)
    level, why = engine._verdict(state, "Done.")
    assert level == "NOT VERIFIED" and "index.html" in why
    assert "game.js" in state.write_problems()[str((project / "index.html").resolve())]


def test_a_file_that_still_does_not_parse_keeps_it(engine, project):
    state = TrajectoryState({}, goal="Write a script.", message_count=1, workspace=str(project))
    _write(engine, state, project / "a.py", "x = (\n")
    _write(engine, state, project / "b.py", "y = 1\n")
    _tested(state)
    assert engine._verdict(state, "Done.")[0] == "NOT VERIFIED"


def test_a_file_that_was_removed_drops_it(engine, project):
    state = TrajectoryState({}, goal="Write a script.", message_count=1, workspace=str(project))
    _write(engine, state, project / "a.py", "x = (\n")
    (project / "a.py").unlink()
    _tested(state)
    assert engine._verdict(state, "Done.")[0] != "NOT VERIFIED"
