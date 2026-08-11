"""The artifacts fixture, and the guard that keeps the repo root clean."""


def test_artifacts_dir_exists_and_is_writable(artifacts_dir):
    target = artifacts_dir / "sample.txt"
    target.write_text("hello")

    assert target.read_text() == "hello"
    assert artifacts_dir.name == "tests"
    assert artifacts_dir.parent.name == "artifacts"


def test_artifacts_dir_is_inside_the_repo_but_not_the_root(artifacts_dir):
    """Generated files belong in artifacts/, never beside pyproject.toml."""
    root = artifacts_dir.parent.parent
    assert (root / "pyproject.toml").is_file()
    assert artifacts_dir.parent != root


def test_new_relative_agent_writes_land_in_artifacts(engine, tmp_path):
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"

    out = engine.run_tool("write_file", {"filename": "library.py", "content": "ok"})

    assert not (tmp_path / "library.py").exists()
    assert (tmp_path / "artifacts" / "library.py").read_text() == "ok"
    assert "artifacts" in out


def test_existing_relative_project_file_is_edited_in_place(engine, tmp_path):
    source = tmp_path / "existing.py"
    source.write_text("old")
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"

    engine.run_tool("write_file", {"filename": "existing.py", "content": "new"})

    assert source.read_text() == "new"
    assert not (tmp_path / "artifacts" / "existing.py").exists()
