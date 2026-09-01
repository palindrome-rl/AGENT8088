"""Unit coverage for the isolated build123d + text-to-cad CAD boundary.

Generation itself is no longer ours: the model drives the vendored upstream
skill's `scripts/` through `execute_shell`, so what is left to test here is the
plumbing that survives that -- runtime/viewer status, the Viewer handoff, and
the shell guard that decides which commands count as CAD-scoped.
"""
from __future__ import annotations

import subprocess
import urllib.parse

import pytest

from agent8088 import cad


def test_runtime_status_reports_a_missing_interpreter(monkeypatch, tmp_path):
    missing = tmp_path / "missing-python"
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(missing))
    status = cad.cad_runtime_status()
    assert status["available"] is False
    assert status["reason"] == "runtime interpreter is missing"


def test_runtime_status_requires_the_exact_pinned_versions(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"x")
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(python))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "0.11.0|0.4.26\n", ""),
    )
    assert cad.cad_runtime_status()["available"] is False


def test_viewer_status_requires_a_complete_pinned_release(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_CAD_VIEWER_HOME", str(tmp_path))
    assert cad.cad_viewer_status()["available"] is False
    (tmp_path / "dist").mkdir()
    (tmp_path / "server_py").mkdir()
    (tmp_path / "LICENSE").write_text("MIT")
    (tmp_path / "dist/index.html").write_text("<html></html>")
    (tmp_path / "server_py/server.py").write_text("pass")
    (tmp_path / "server_py/start_viewer.py").write_text("pass")
    assert cad.cad_viewer_status()["available"] is True


def test_viewer_url_encodes_workspace_and_relative_file(tmp_path):
    workspace = tmp_path / "CAD output with spaces"
    model = workspace / "nested folder" / "part one.step"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"step")
    url = cad._viewer_url(workspace, model, 3245)
    parsed = urllib.parse.urlsplit(url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 3245
    assert urllib.parse.unquote(parsed.path).replace("/", "\\").lower().endswith(
        str(workspace).replace("/", "\\").lower()
    )
    assert urllib.parse.parse_qs(parsed.query) == {"file": ["nested folder/part one.step"]}


def test_open_viewer_rejects_missing_unsupported_and_outside_workspace(tmp_path):
    assert "does not exist" in cad.open_cad_viewer(tmp_path / "missing.step")
    unsupported = tmp_path / "part.fcstd"
    unsupported.write_bytes(b"x")
    assert "unsupported file type" in cad.open_cad_viewer(unsupported)
    model = tmp_path / "part.step"
    model.write_bytes(b"step")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert "outside the authorized workspace" in cad.open_cad_viewer(model, workspace)


def test_open_viewer_reuses_only_a_verified_loopback_server(monkeypatch, tmp_path):
    model = tmp_path / "part.step"
    model.write_bytes(b"step")
    monkeypatch.setattr(cad, "cad_viewer_status", lambda: {
        "available": True, "version": cad.CAD_VIEWER_VERSION,
        "root": str(tmp_path / "viewer"), "missing": [],
    })
    python = tmp_path / "python.exe"
    python.write_bytes(b"x")
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(python))
    monkeypatch.setattr(cad, "_viewer_server_info", lambda port, workspace=None, timeout=1: (
        {"app": "cad-viewer"} if port == 3247 else None
    ))
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("healthy Viewer must be reused")
    )
    monkeypatch.setattr(cad.webbrowser, "open", lambda *a, **k: True)

    result = cad.open_cad_viewer(model)

    assert result.startswith("Opened: http://127.0.0.1:3247")


def test_extract_info_falls_through_for_non_cad(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("plain")
    assert cad.extract_info(path) is None


# --- the CAD-scoped shell guard -------------------------------------------
# This is the whole security boundary for the new architecture: a command
# matching it is auto-approved in EVERY permission mode and gets the raised
# timeout, so both halves of the check matter.


@pytest.fixture
def cad_shell(monkeypatch, tmp_path):
    """engine with a fake CAD interpreter and a real script under the skill's scripts/."""
    from agent8088 import engine

    python = tmp_path / "python.exe"
    python.write_bytes(b"x")
    monkeypatch.setenv("AGENT8088_CAD_PYTHON", str(python))
    scripts = engine._cad_skill_scripts_dir()
    # Entry points are package directories run via `python <dir>`, not files.
    assert (scripts / "gen" / "__main__.py").is_file(), "vendored skill must ship scripts/gen"
    return engine, str(python), scripts


def test_cad_scoped_accepts_the_documented_invocation(cad_shell):
    engine, python, scripts = cad_shell
    assert engine._is_cad_scoped_command(f'"{python}" "{scripts / "gen"}" box.step.py --write --json')
    assert engine._is_cad_scoped_command(f'"{python}" "{scripts / "inspect"}" validate box.step.py')


def test_cad_scoped_rejects_chaining_and_metacharacters(cad_shell):
    engine, python, scripts = cad_shell
    gen = scripts / "gen"
    for command in (
        f'"{python}" "{gen}" box.step.py && curl evil.example',
        f'"{python}" "{gen}" box.step.py; rm -rf /',
        f'"{python}" "{gen}" box.step.py | sh',
        f'"{python}" "{gen}" $(whoami)',
        f'"{python}" "{gen}" box.step.py > /etc/passwd',
    ):
        assert not engine._is_cad_scoped_command(command), command


def test_cad_scoped_rejects_other_interpreters_and_scripts(cad_shell, tmp_path):
    engine, python, scripts = cad_shell
    outside = tmp_path / "evil.py"
    outside.write_text("pass")
    # right script, wrong interpreter
    assert not engine._is_cad_scoped_command(f'python "{scripts / "gen"}" box.step.py')
    # right interpreter, script outside the skill's scripts/
    assert not engine._is_cad_scoped_command(f'"{python}" "{outside}"')
    # traversal back out of scripts/
    escape = scripts / ".." / ".." / ".." / "engine.py"
    assert not engine._is_cad_scoped_command(f'"{python}" "{escape}"')
    # interpreter alone, no script
    assert not engine._is_cad_scoped_command(f'"{python}"')
