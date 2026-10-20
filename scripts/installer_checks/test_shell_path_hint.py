"""A write result says how the shell reaches the file when a bare name would not work from the shell's folder.

The file tools and the shell resolve paths differently (a bare invented name goes to artifacts/, a path the user typed
stays in the project folder, and the shell runs in artifacts/ or, after the user's own path was written, in the project
folder), so a model that wrote a file and then ran it from the shell had to guess where the shell would look."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def roots(engine, tmp_path, monkeypatch):
    project, artifacts = tmp_path / "work", tmp_path / "work" / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "_shell_workspace_root", None)
    return project, artifacts


def _sandbox(engine, monkeypatch, active):
    entry = None if active is None else SimpleNamespace(active=active)
    monkeypatch.setattr(engine.capabilities, "get", lambda name: entry)


def test_no_sandbox_adds_nothing(engine, roots, monkeypatch):
    _sandbox(engine, monkeypatch, None)
    assert engine._shell_path_hint(roots[1] / "a.py") == ""
    _sandbox(engine, monkeypatch, "")
    assert engine._shell_path_hint(roots[1] / "a.py") == ""


def test_a_file_in_the_shell_folder_adds_nothing(engine, roots, monkeypatch):
    _sandbox(engine, monkeypatch, "native")
    assert engine._shell_path_hint(roots[1] / "app" / "main.py") == ""


def test_a_project_file_outside_the_shell_folder_gets_its_full_path(engine, roots, monkeypatch):
    project, artifacts = roots
    _sandbox(engine, monkeypatch, "native")
    target = project / "fib.py"
    hint = engine._shell_path_hint(target)
    assert f"shell commands run in {artifacts.resolve()}" in hint
    assert f"use its full path {target}" in hint


def test_after_the_shell_moved_to_the_project_folder_an_artifacts_file_gets_its_relative_path(engine, roots, monkeypatch):
    project, artifacts = roots
    monkeypatch.setattr(engine, "_shell_workspace_root", project)
    _sandbox(engine, monkeypatch, "native")
    hint = engine._shell_path_hint(artifacts / "data.json")
    assert str(Path("artifacts") / "data.json") in hint
    assert str(project.resolve()) in hint


def test_a_project_file_is_fine_once_the_shell_is_in_the_project_folder(engine, roots, monkeypatch):
    project, _ = roots
    monkeypatch.setattr(engine, "_shell_workspace_root", project)
    _sandbox(engine, monkeypatch, "native")
    assert engine._shell_path_hint(project / "fib.py") == ""


def test_docker_keeps_its_existing_wording(engine, roots, monkeypatch):
    project, artifacts = roots
    _sandbox(engine, monkeypatch, "docker")
    assert engine._shell_path_hint(artifacts / "app" / "main.py") == \
        " (shell commands see it as /workspace/app/main.py)"
    assert "cannot see this file" in engine._shell_path_hint(project / "x.py")


@pytest.mark.parametrize("backend", ["native", "docker", ""])
@pytest.mark.parametrize("operation", ["write_file", "append", "edit_file", "fetch", "copy_file", "move_file"])
def test_the_hint_reaches_actual_file_tool_results(engine, roots, monkeypatch, backend, operation):
    project, artifacts = roots
    _sandbox(engine, monkeypatch, backend)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [project])
    monkeypatch.setattr(engine, "_shell_workspace_root", project)
    target = artifacts / "note.txt"
    tool = operation
    args = {"filename": str(target), "content": "hello"}
    if operation == "edit_file":
        target.write_text("old", encoding="utf-8")
        args = {"filename": str(target), "old_string": "old", "new_string": "hello"}
    elif operation == "append":
        tool = "write_file"
        args["mode"] = "append"
    elif operation == "fetch":
        tool = "write_file"
        monkeypatch.setattr(engine, "_fetch_url_bytes", lambda url: (b"hello", None, "text/plain"))
        args = {"filename": str(target), "source_url": "https://example.com/note.txt"}
    elif operation in ("copy_file", "move_file"):
        source = artifacts / "source.txt"
        source.write_text("hello", encoding="utf-8")
        args = {"source": str(source), "destination": str(target)}

    result = engine.exec_tool(tool, json.dumps(args))
    assert target.read_text(encoding="utf-8") == "hello", result
    if backend == "native":
        assert f"from the shell's folder {project.resolve()} this file is" in result, result
        assert str(Path("artifacts") / "note.txt") in result, result
    elif backend == "docker":
        assert " (shell commands see it as /workspace/artifacts/note.txt)" in result, result
    else:
        assert "shell commands" not in result and "shell's folder" not in result, result


def test_a_user_named_write_reports_the_workspace_it_just_selected(engine, roots, monkeypatch):
    project, artifacts = roots
    _sandbox(engine, monkeypatch, "native")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [project])
    target = project / "named.txt"
    monkeypatch.setattr(engine, "_CURRENT_RUN_MESSAGES", [{"role": "user", "content": f"Write {target}"}])
    result = engine.exec_tool("write_file", json.dumps({"filename": str(target), "content": "hello"}))
    assert target.read_text(encoding="utf-8") == "hello", result
    assert engine._shell_workspace_root == project
    assert "outside this file's folder" not in result, result
