"""explore and researcher are pinned read-only, without losing what they are for.

A profile's tool list said "read-only" in its description, but nothing enforced it:
explore carries execute_shell, so it could change the workspace like any coder.
The pin makes that a guarantee. It must not cost the profiles their job, though:
a pinned child still searches the web when its parent may, and its shell commands
still see the folder the parent's shell works in.
"""
import json
import shutil
from pathlib import Path

import pytest


@pytest.mark.parametrize("profile", ["explore", "researcher", "auditor"])
def test_observing_profiles_are_pinned_readonly(engine, profile):
    specs = engine.load_subagent_specs(engine.AGENTS_DIR, None)
    assert specs[profile]["permission"] == "readonly"


@pytest.mark.parametrize("profile", ["coder", "test-writer", "general-purpose"])
def test_working_profiles_are_not_pinned(engine, profile):
    specs = engine.load_subagent_specs(engine.AGENTS_DIR, None)
    assert specs[profile]["permission"] == ""


def _spawn_schema(engine):
    tools = engine.build_tools_def(engine.TOOL_SPECS)
    spawn = next(t for t in tools if t["function"]["name"] == "spawn_subagent")
    return spawn["function"]["parameters"]["properties"]["agent_type"]


def test_agent_type_lists_the_valid_profiles(engine):
    schema = _spawn_schema(engine)
    assert schema["type"] == "string"
    assert schema["enum"] == sorted(engine.SUBAGENT_SPECS)


def test_agent_type_follows_profiles_added_later(engine, monkeypatch):
    specs = dict(engine.SUBAGENT_SPECS, reviewer={"name": "reviewer"})
    monkeypatch.setattr(engine, "SUBAGENT_SPECS", specs)
    assert "reviewer" in _spawn_schema(engine)["enum"]


def test_other_tools_get_no_enum(engine):
    tools = engine.build_tools_def(engine.TOOL_SPECS)
    for tool in tools:
        if tool["function"]["name"] == "spawn_subagent":
            continue
        for prop in tool["function"]["parameters"].get("properties", {}).values():
            assert "enum" not in prop or tool["function"]["name"].startswith("mcp")


def _pin(engine, monkeypatch, parent_mode):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "_permission_floor_readonly", True)
    monkeypatch.setattr(engine, "_floor_parent_mode", parent_mode)


@pytest.mark.parametrize("parent_mode", ["edit", "full-auto"])
@pytest.mark.parametrize("mode", ["search", "http_get"])
def test_a_pinned_child_reads_the_web_when_its_parent_may(engine, monkeypatch, parent_mode, mode):
    _pin(engine, monkeypatch, parent_mode)
    assert engine.check_permission(mode, "https://example.com") is True


@pytest.mark.parametrize("mode", ["search", "http_get"])
def test_a_pinned_child_never_gets_more_network_than_a_readonly_parent(engine, monkeypatch, mode):
    _pin(engine, monkeypatch, "readonly")
    assert engine.check_permission(mode, "https://example.com") is False


@pytest.mark.parametrize("mode", ["http_post", "write_text", "browser", "cron"])
def test_the_pin_still_refuses_changes_under_a_full_auto_parent(engine, monkeypatch, mode):
    _pin(engine, monkeypatch, "full-auto")
    assert engine.check_permission(mode, "anything") is False


def test_without_a_pin_the_network_rule_is_unchanged(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "_permission_floor_readonly", False)
    monkeypatch.setattr(engine, "_floor_parent_mode", "full-auto")
    assert engine.check_permission("search", "q") is False


def _readonly_shell(engine, monkeypatch, tmp_path, project):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "made_by_agent.txt").write_text("artifact", encoding="utf-8")
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "_shell_workspace_root", project)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_sandbox_readonly", True)
    monkeypatch.setattr(engine, "_explain_sandbox_denial", lambda result, *args: result)
    seen = {}

    def run(backend, command, timeout, python_code, image, workspace):
        seen["command"] = command
        seen["files"] = sorted(p.relative_to(workspace).as_posix()
                               for p in Path(workspace).rglob("*") if p.is_file())
        (Path(workspace) / "src" / "app.py").write_text("changed", encoding="utf-8")
        return "ran"

    monkeypatch.setattr(engine, "_run_in_sandbox_backend", run)
    return seen


def test_readonly_shell_sees_the_project_folder_the_parent_works_in(engine, monkeypatch, tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("print(1)", encoding="utf-8")
    (project / ".env").write_text("TOKEN=1", encoding="utf-8")
    seen = _readonly_shell(engine, monkeypatch, tmp_path, project)

    result = engine._run_sandbox_command(f"cat {project / 'src' / 'app.py'}", 10)

    assert result.startswith("ran")
    assert "src/app.py" in seen["files"]
    assert ".env" not in seen["files"]
    assert str(project) not in seen["command"]          # pointed at the copy instead
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == "print(1)"


def test_readonly_shell_without_a_project_folder_still_copies_artifacts(engine, monkeypatch, tmp_path):
    seen = _readonly_shell(engine, monkeypatch, tmp_path, None)
    (tmp_path / "artifacts" / "src").mkdir()
    (tmp_path / "artifacts" / "src" / "app.py").write_text("x", encoding="utf-8")
    assert engine._run_sandbox_command("ls", 10).startswith("ran")
    assert "made_by_agent.txt" in seen["files"]


def test_a_folder_too_large_to_copy_is_refused_with_a_way_forward(engine, monkeypatch, tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    for i in range(6):
        (project / f"f{i}.txt").write_text("x", encoding="utf-8")
    seen = _readonly_shell(engine, monkeypatch, tmp_path, project)
    monkeypatch.setattr(engine, "_READONLY_COPY_MAX_FILES", 5)

    result = engine._run_sandbox_command("ls", 10)

    assert result.startswith("Error:")
    assert "read_text" in result and "repository_read" in result
    assert "Nothing was run" in result
    assert "files" not in seen
