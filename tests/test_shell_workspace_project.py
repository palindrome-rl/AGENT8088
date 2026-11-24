"""Shell commands start in the project folder and may write there; the repository's .git stays read-only.

With only artifacts/ writable, a build that writes next to its own sources (out/, dist/, a venv) was refused, and
the model either gave up or rebuilt the project inside artifacts/. shell_workspace=project lets the shell work in the
project itself, never in a folder that holds the user's home, and keeps .git out of reach on every backend: srt by a
write deny, Docker by a read-only mount, the Windows runner (which grants one folder whole) by putting the hooks and
config back after each command."""
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def project(engine, tmp_path, monkeypatch):
    root = tmp_path / "work"
    (root / "artifacts").mkdir(parents=True)
    (root / ".git" / "hooks").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
    (root / ".git" / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(engine, "PROJECT_ROOT", root)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", root / "artifacts")
    monkeypatch.setattr(engine, "SHELL_WORKSPACE", "project")
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "_path_is_allowed", lambda path: True)
    monkeypatch.setattr(engine.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "home" / ".agent8088")
    monkeypatch.setattr(engine, "_shell_workspace_root", None)
    return root


def test_the_shell_starts_in_the_project_folder(engine, project):
    assert engine._project_shell_root() == project.resolve()
    engine.reset_turn_counters()
    assert engine._shell_workspace_root == project.resolve()


def test_the_artifacts_setting_keeps_the_old_start(engine, project, monkeypatch):
    monkeypatch.setattr(engine, "SHELL_WORKSPACE", "artifacts")
    engine.reset_turn_counters()
    assert engine._shell_workspace_root is None


def test_a_disposable_container_keeps_its_own_rules(engine, project, monkeypatch):
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    assert engine._project_shell_root() is None


@pytest.mark.parametrize("where", ["home", "above home", "drive root", "agent data"])
def test_a_folder_too_wide_for_a_shell_keeps_artifacts(engine, project, tmp_path, monkeypatch, where):
    home = tmp_path / "home"
    root = {"home": home, "above home": tmp_path, "drive root": Path(tmp_path.anchor),
            "agent data": home / ".agent8088"}[where]
    if where != "drive root":
        root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", root)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", root / "artifacts")
    assert engine._project_shell_root() is None


def test_a_folder_outside_the_allowed_paths_keeps_artifacts(engine, project, monkeypatch):
    monkeypatch.setattr(engine, "_path_is_allowed", lambda path: False)
    assert engine._project_shell_root() is None


def test_srt_may_write_the_project_but_not_its_git(engine, project, monkeypatch):
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    settings = engine._sandbox_settings_data(False, project)
    assert str(project.resolve()) in settings["filesystem"]["allowWrite"]
    assert str((project / ".git").resolve()) in settings["filesystem"]["denyWrite"]


def test_a_read_only_run_protects_nothing_extra(engine, project, monkeypatch):
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    settings = engine._sandbox_settings_data(True, project)
    assert str((project / ".git").resolve()) not in settings["filesystem"]["denyWrite"]


def test_a_linked_worktree_git_file_is_protected_too(engine, project):
    import shutil
    shutil.rmtree(project / ".git")
    (project / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert engine._protected_repo_paths(project) == [(project / ".git").resolve()]


def test_the_windows_guard_puts_back_hooks_and_config(engine, project):
    saved = engine._git_guard_snapshot(project)
    (project / ".git" / "hooks" / "post-checkout").write_text("evil\n", encoding="utf-8")
    (project / ".git" / "config").write_text("[core]\n\tfsmonitor = evil\n", encoding="utf-8")
    (project / ".git" / "hooks" / "pre-commit.sample").unlink()
    note = engine._git_guard_restore(project, saved)
    assert not (project / ".git" / "hooks" / "post-checkout").exists()
    assert "fsmonitor" not in (project / ".git" / "config").read_text(encoding="utf-8")
    assert (project / ".git" / "hooks" / "pre-commit.sample").exists()
    assert "were put back" in note and "hooks/post-checkout" in note


def test_the_windows_guard_is_silent_when_nothing_changed(engine, project):
    saved = engine._git_guard_snapshot(project)
    (project / "out").mkdir()
    assert engine._git_guard_restore(project, saved) == ""
    assert engine._git_guard_snapshot(project / "artifacts") is None


def test_docker_mounts_git_read_only(engine, project, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_docker_sandbox_broken", False)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    monkeypatch.setattr(engine, "_docker_egress_args", lambda: ("none", []))
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=25: seen.setdefault("argv", argv) and "ok")
    engine._exec_docker_command("ls", 10, workspace=project)
    mounts = [seen["argv"][i + 1] for i, part in enumerate(seen["argv"]) if part == "--mount"]
    assert f"type=bind,src={(project / '.git').resolve()},dst=/workspace/.git,readonly" in mounts
    seen.clear()
    engine._exec_docker_command("ls", 10, workspace=project, readonly=True)
    assert not any("dst=/workspace/.git" in part for part in seen["argv"])


def test_the_end_note_does_not_say_work_was_moved(engine, project, monkeypatch):
    monkeypatch.setattr(engine, "_shell_workspace_root", project.resolve())
    monkeypatch.setattr(engine, "_redirected_write_locations", lambda trajectory: [])
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    trajectory = SimpleNamespace(mutation_count=lambda: 3)
    assert engine._workspace_note(trajectory) == ""
    monkeypatch.setattr(engine, "_shell_workspace_root", None)
    assert "Work ran in the sandbox workspace" in engine._workspace_note(trajectory)


def test_the_shell_tool_says_commands_work_in_the_project(engine, project):
    specs = {"execute_shell": {"description": "Run a command."}}
    engine._ground_execute_shell_description(specs)
    text = specs["execute_shell"]["description"]
    assert f"Working directory: {project} (the project folder; commands may write here, except in .git." in text
