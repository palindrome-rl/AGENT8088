"""Issue-sheet row S39: the smoke/boot gate must cover what it says it covers.

The runner only accepted scripts under ARTIFACTS_ROOT, so a script written at
a path the user named in their own request got no runner and the check
reported "compiles; not executed" as a pass.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent8088 import engine  # noqa: E402


@pytest.fixture
def sandboxed(monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_run_sandbox_command", lambda cmd, timeout: "ok")
    monkeypatch.setattr(engine, "_smoke_cd_prefix", lambda here, rel, native: "cd %s && " % here)
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda root, readonly=False: False)
    return tmp_path


def test_a_script_under_artifacts_still_gets_a_runner(sandboxed, monkeypatch):
    monkeypatch.setattr(engine, "_shell_workspace_root", None)
    target = sandboxed / "artifacts" / "app.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    assert engine._smoke_script_runner(target) is not None


def test_a_script_at_a_user_named_project_path_gets_a_runner(sandboxed, monkeypatch):
    """The regression: this used to be refused and silently passed."""
    project = sandboxed / "myapp"
    project.mkdir()
    target = project / "app.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_shell_workspace_root", project)
    assert engine._smoke_script_runner(target) is not None


def test_a_script_outside_the_mounted_workspace_gets_no_runner(sandboxed, monkeypatch):
    project = sandboxed / "myapp"
    project.mkdir()
    other = sandboxed / "elsewhere"
    other.mkdir()
    target = other / "app.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    # The project is mounted, but this file is not under it.
    monkeypatch.setattr(engine, "_shell_workspace_root", project)
    assert engine._smoke_script_runner(target) is None


def test_no_sandbox_backend_means_no_runner(sandboxed, monkeypatch):
    monkeypatch.setattr(engine, "_shell_workspace_root", None)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "unavailable")
    target = sandboxed / "artifacts" / "app.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    assert engine._smoke_script_runner(target) is None