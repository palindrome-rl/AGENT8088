"""The workspace is never the home folder, a drive root or a system folder by accident.

Seen in a live run: agent8088 started from a terminal that opened in the home
folder, so the home folder became the project. New files landed loose in it, and
once the user named a path there the shell's writable folder became the whole
home folder. The sandbox's write rule is "the workspace", so that rule then
covered nearly everything, and only the deny list stood between a command and
the user's files.

An unset project_root that resolves to such a folder now moves to
~/agent8088-workspace, with a notice. Paths elsewhere in the home folder can still
be named. A project_root the user set on purpose is kept, with a warning.
"""
from pathlib import Path

import pytest


@pytest.fixture
def home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def warnings(engine, monkeypatch):
    collected = []
    monkeypatch.setattr(engine, "CONFIG_WARNINGS", collected)
    return collected


def test_launching_from_home_moves_the_workspace(engine, home, warnings):
    chosen = engine._configured_project_root({}, home)
    assert chosen == (home / "agent8088-workspace").resolve()
    assert chosen.is_dir()
    assert any("home folder" in w and "agent8088-workspace" in w for w in warnings)


def test_a_project_folder_is_left_alone(engine, home, warnings):
    project = home / "code" / "app"
    project.mkdir(parents=True)
    assert engine._configured_project_root({}, project) == project.resolve()
    assert warnings == []


def test_a_drive_or_filesystem_root_is_moved(engine, home, warnings):
    root = Path(home.anchor)
    assert engine._configured_project_root({}, root) == (home / "agent8088-workspace").resolve()
    assert any("root" in w for w in warnings)


def test_a_system_folder_is_moved(engine, home, warnings, monkeypatch, tmp_path):
    system = tmp_path / "Windows"
    system.mkdir()
    monkeypatch.setattr(engine, "_system_folders", lambda: [system.resolve()])
    assert engine._configured_project_root({}, system) == (home / "agent8088-workspace").resolve()
    assert any("system folder" in w for w in warnings)


def test_an_explicit_home_project_root_is_kept_with_a_warning(engine, home, warnings, tmp_path):
    launch = tmp_path / "elsewhere"
    launch.mkdir()
    chosen = engine._configured_project_root({"project_root": str(home)}, launch)
    assert chosen == home.resolve()
    assert any("project_root" in w and "home folder" in w for w in warnings)


def test_allowed_paths_that_exclude_the_new_folder_keep_the_old_choice(engine, home, warnings):
    # allowed_paths names only the home folder's own documents, so the new folder
    # would be refused for writing; moving there would break every write.
    docs = home / "Documents"
    docs.mkdir()
    chosen = engine._configured_project_root({"allowed_paths": str(docs)}, home)
    assert chosen == docs.resolve()


def test_allowed_paths_covering_home_still_move_the_workspace(engine, home, warnings):
    chosen = engine._configured_project_root({"allowed_paths": str(home)}, home)
    assert chosen == (home / "agent8088-workspace").resolve()


def test_the_default_allowed_paths_keep_the_home_folder_reachable(engine, home):
    assert engine._default_allowed_paths(home / "agent8088-workspace", home) == \
        [str(home / "agent8088-workspace"), str(home)]
    project = home / "code"
    assert engine._default_allowed_paths(project, None) == [str(project)]


def test_a_filesystem_root_is_not_added_back_to_allowed_paths(engine, home):
    root = Path(home.anchor)
    assert engine._default_allowed_paths(home / "agent8088-workspace", root) == \
        [str(home / "agent8088-workspace")]
