"""A path the agent saw inside the sandbox can be read back on the host.

Shell tools run in the container, where the workspace is bind-mounted at
/workspace — so `ls` reports /workspace/library.py. read_text runs on the host,
where that string is drive-relative and resolves to C:\\workspace\\library.py.
The agent listed a file, passed the same path straight back, and was told
`Path not allowed: C:\\workspace\\library.py` — naming a location nobody had
mentioned and no directory that exists.
"""
import pytest


def test_a_container_path_maps_to_the_host_workspace(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    target = tmp_path / "library.py"
    target.write_text("x", encoding="utf-8")

    assert engine.resolve_user_path("/workspace/library.py") == target.resolve()


def test_the_bare_container_root_maps_too(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])

    assert engine.resolve_user_path("/workspace") == tmp_path.resolve()


def test_nested_container_paths_map(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    nested = tmp_path / "tests" / "test_x.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("x", encoding="utf-8")

    assert engine.resolve_user_path("/workspace/tests/test_x.py") == nested.resolve()


def test_writes_to_a_container_path_land_on_the_host(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])

    assert engine.resolve_write_path("/workspace/new.txt") == (tmp_path / "new.txt").resolve()


def test_escaping_the_workspace_is_still_refused(engine, tmp_path, monkeypatch):
    """The prefix is rewritten; the allowed-path check still has the last word."""
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])

    with pytest.raises(ValueError, match="Path not allowed"):
        engine.resolve_user_path("/workspace/../../../etc/passwd")


def test_a_real_host_path_is_untouched(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")

    assert engine.resolve_user_path(str(target)) == target.resolve()


def test_a_similarly_named_path_is_not_rewritten(engine, tmp_path, monkeypatch):
    """/workspaces and /workspace-old are not the mount point."""
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])

    assert engine._from_container_path("/workspaces/x") == "/workspaces/x"
    assert engine._from_container_path("/workspace-old/x") == "/workspace-old/x"


def test_relative_names_are_unaffected(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    assert engine._from_container_path("library.py") == "library.py"
