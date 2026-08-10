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
