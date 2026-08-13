"""New files the agent invents belong in artifacts/, not the project root.

A plan built a library manager: `library.py` plus a `library.json` data file.
`library.json` was new, so it was routed to `artifacts/`. `library.py` was NOT
routed, because a file of that name was already sitting at the project root from
an earlier run — `resolve_write_path` read mere existence as "this is an edit,
keep it in place". The program ended up split across two directories, could not
run, and the read-only auditor (which resolves a bare name against the sandbox
workspace, i.e. artifacts/) reported the file missing.

The rule: a bare filename does not specify a location, so it always lands in
artifacts/. A path that names a directory does specify one, and is honoured.
"""
import pytest


@pytest.fixture
def project(engine, tmp_path, monkeypatch):
    """An isolated PROJECT_ROOT with its own artifacts/ — never the real repo."""
    root = tmp_path.resolve()
    (root / "artifacts").mkdir()
    monkeypatch.setattr(engine, "PROJECT_ROOT", root)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", root / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [root])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    return root


def test_a_bare_name_goes_to_artifacts_when_nothing_exists_yet(engine, project):
    assert engine.resolve_write_path("library.py") == project / "artifacts" / "library.py"


def test_a_bare_name_goes_to_artifacts_even_when_the_root_has_one(engine, project):
    """The defect: a leftover at the root pinned every later write to the root."""
    (project / "library.py").write_text("# left over from an earlier run\n")

    assert engine.resolve_write_path("library.py") == project / "artifacts" / "library.py"


def test_a_program_and_its_data_file_land_in_the_same_directory(engine, project):
    """The split that broke the run: source at the root, data in artifacts/."""
    (project / "library.py").write_text("# left over\n")

    source = engine.resolve_write_path("library.py")
    data = engine.resolve_write_path("library.json")

    assert source.parent == data.parent == project / "artifacts"


def test_a_leftover_at_the_root_is_left_untouched(engine, project):
    """Diverting the write must not disturb whatever was already there."""
    leftover = project / "library.py"
    leftover.write_text("# left over from an earlier run\n")

    engine.run_tool("write_file", {"filename": "library.py", "content": "new"})

    assert leftover.read_text() == "# left over from an earlier run\n"
    assert (project / "artifacts" / "library.py").read_text() == "new"


def test_a_diverted_write_tells_the_model_where_it_went(engine, project):
    """Silent divergence is unrecoverable; a named path is not."""
    (project / "README.md").write_text("real project file\n")

    out = engine.run_tool("write_file", {"filename": "README.md", "content": "x"})

    assert str(project / "artifacts" / "README.md") in out
    assert "absolute path" in out, "the model needs to be told how to reach the other one"


def test_an_explicit_relative_path_still_edits_the_project_file_in_place(engine, project):
    """A directory component specifies a location, so it is honoured."""
    (project / "src").mkdir()
    (project / "src" / "engine.py").write_text("real source\n")

    assert engine.resolve_write_path("src/engine.py") == project / "src" / "engine.py"


def test_an_absolute_path_to_an_existing_file_still_edits_it_in_place(engine, project):
    target = project / "pyproject.toml"
    target.write_text("[project]\n")

    assert engine.resolve_write_path(str(target)) == target


def test_a_new_relative_path_under_a_project_directory_still_goes_to_artifacts(engine, project):
    """Unchanged: only files that already exist are edited where they sit."""
    (project / "src").mkdir()

    assert engine.resolve_write_path("src/new.py") == project / "artifacts" / "src" / "new.py"


def test_a_write_already_aimed_at_artifacts_is_not_nested_twice(engine, project):
    assert engine.resolve_write_path("artifacts/notes.txt") == project / "artifacts" / "notes.txt"


def test_the_auditor_looks_where_a_bare_name_was_actually_written(engine, project, monkeypatch):
    """The invariant that broke.

    run_sandboxed carries no path argument, so the auditor is given the workspace
    paths and resolves a bare name against the first of them — artifacts/. When a
    leftover at the root pulled the write out of artifacts/, the auditor was
    looking in the one place the file was not.
    """
    (project / "library.py").write_text("# left over\n")
    seen = {}
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda a, depth=0: seen.update(task=a["task"]) or "VERDICT: pass — ok")
    engine._active_budget = engine._TurnBudget()

    engine._audit_plan_step("run the demo", "run_sandboxed",
                            {"code": "import json"}, "ran", 0)
    written = engine.resolve_write_path("library.py")

    hint = seen["task"].split("resolve any relative name against these): ")[1]
    assert str(written.parent) == hint.splitlines()[0].split(", ")[0]
