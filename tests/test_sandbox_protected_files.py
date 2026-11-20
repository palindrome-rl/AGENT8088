"""Secret files are protected by the native sandbox on Linux too.

sandbox-runtime applies `allowWrite`/`denyWrite` on Linux as literal paths only:
any other glob pattern is skipped, and a skipped pattern protects nothing. Its
`denyRead` globs are expanded once, when the command is wrapped, and only from a
literal starting directory. The settings used patterns such as
`<project>/**/*.pem` for both lists, so on Linux the write protection for secret
files never applied, and the workspace the command runs in was not covered at all
when it sat outside the project.

The settings are written before every command, so expanding the patterns to the
files that exist at that moment gives Linux the same protection macOS gets from
its patterns.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _tree(root: Path) -> dict:
    files = {
        "app.py": "print(1)",
        ".env": "TOKEN=1",
        ".env.local": "TOKEN=2",
        "certs/server.pem": "pem",
        "certs/client.key": "key",
        "deep/a/b/store.p12": "p12",
        "config/API_KEY.txt": "k",
        "config/db_PASSWORD": "p",
        "notes/readme.md": "hello",
        "node_modules/pkg/index.js": "x",
        ".git/config": "[core]",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return files


SECRETS = (".env", ".env.local", "certs/server.pem", "certs/client.key",
           "deep/a/b/store.p12", "config/API_KEY.txt", "config/db_PASSWORD")


def _settings(engine, monkeypatch, tmp_path, platform, workspace=None):
    project = tmp_path / "project"
    project.mkdir()
    _tree(project)
    monkeypatch.setattr(engine.sys, "platform", platform)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", project / "artifacts")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    return project, engine._sandbox_settings_data(readonly=False, workspace=workspace)


def _resolved(paths):
    return {str(Path(p)) for p in paths}


def test_linux_settings_list_existing_secret_files_literally(engine, monkeypatch, tmp_path):
    project, data = _settings(engine, monkeypatch, tmp_path, "linux")
    fs = data["filesystem"]
    for rel in SECRETS:
        literal = str((project / rel).resolve())
        assert literal in _resolved(fs["denyRead"]), rel
        assert literal in _resolved(fs["denyWrite"]), rel
    assert str((project / "notes" / "readme.md").resolve()) not in _resolved(fs["denyRead"])


def test_linux_settings_carry_no_patterns_srt_would_skip(engine, monkeypatch, tmp_path):
    _, data = _settings(engine, monkeypatch, tmp_path, "linux")
    fs = data["filesystem"]
    for entry in fs["denyWrite"] + fs["allowWrite"]:
        assert "*" not in entry, entry
    for entry in fs["denyRead"]:
        first = Path(entry).parts[1] if len(Path(entry).parts) > 1 else ""
        assert "*" not in first, entry


def test_linux_settings_cover_a_workspace_outside_the_project(engine, monkeypatch, tmp_path):
    workspace = tmp_path / "elsewhere"
    workspace.mkdir()
    (workspace / ".env").write_text("TOKEN=3", encoding="utf-8")
    (workspace / "id.pem").write_text("pem", encoding="utf-8")
    _, data = _settings(engine, monkeypatch, tmp_path, "linux", workspace=workspace)
    fs = data["filesystem"]
    for name in (".env", "id.pem"):
        literal = str((workspace / name).resolve())
        assert literal in _resolved(fs["denyRead"])
        assert literal in _resolved(fs["denyWrite"])


def test_macos_keeps_its_patterns_and_adds_the_workspace(engine, monkeypatch, tmp_path):
    workspace = tmp_path / "elsewhere"
    workspace.mkdir()
    project, data = _settings(engine, monkeypatch, tmp_path, "darwin", workspace=workspace)
    deny = data["filesystem"]["denyRead"]
    assert any(entry.endswith(str(Path("**") / "*.pem")) for entry in deny)
    assert any(entry.startswith(str(workspace.resolve())) and "*" in entry for entry in deny)


def test_the_home_secret_folders_stay_denied_on_every_platform(engine, monkeypatch, tmp_path):
    for platform in ("linux", "darwin"):
        project_dir = tmp_path / platform
        project_dir.mkdir()
        _, data = _settings(engine, monkeypatch, project_dir, platform)
        deny = _resolved(data["filesystem"]["denyRead"])
        assert str((Path.home() / ".ssh").resolve()) in deny
        assert str((Path.home() / ".aws").resolve()) in deny


def test_a_huge_tree_is_scanned_within_a_bound(engine, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(engine, "_SECRET_SCAN_MAX_ENTRIES", 5)
    project, data = _settings(engine, monkeypatch, tmp_path, "linux")
    fs = data["filesystem"]
    # The top of the tree is scanned first, so a root .env is still covered.
    assert str((project / ".env").resolve()) in _resolved(fs["denyRead"])
    assert "secret-file scan stopped" in caplog.text


def test_skipped_folders_are_not_scanned(engine, monkeypatch, tmp_path):
    project, data = _settings(engine, monkeypatch, tmp_path, "linux")
    (project / "node_modules" / ".env").write_text("x", encoding="utf-8")
    data = engine._sandbox_settings_data()
    assert not any("node_modules" in entry for entry in data["filesystem"]["denyRead"])


_REAL_SRT = pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("srt")
                               or not shutil.which("bwrap"), reason="needs Linux with srt and bubblewrap")


def _attack(engine, monkeypatch, tmp_path, project, workspace):
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", project / "artifacts")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    (workspace / "notes.txt").write_text("visible-control", encoding="utf-8")
    settings = engine._write_sandbox_settings(readonly=False, workspace=workspace)
    return subprocess.run(
        ["srt", "--settings", str(settings), "-c",
         f"cd {workspace} && cat notes.txt; cat .env; echo changed > server.pem; echo done"],
        capture_output=True, text=True, timeout=60)


@_REAL_SRT
def test_attack_on_secrets_in_the_project_fails_in_the_real_sandbox(engine, monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("TOKEN=real", encoding="utf-8")
    (project / "server.pem").write_text("pem", encoding="utf-8")
    result = _attack(engine, monkeypatch, tmp_path, project, project)
    assert "visible-control" in result.stdout, result.stdout + result.stderr  # the folder is reachable
    assert "TOKEN=real" not in result.stdout
    assert (project / "server.pem").read_text(encoding="utf-8") == "pem"


@_REAL_SRT
def test_attack_on_secrets_in_a_workspace_outside_the_project_fails(engine, monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("TOKEN=real", encoding="utf-8")
    (workspace / "server.pem").write_text("pem", encoding="utf-8")
    result = _attack(engine, monkeypatch, tmp_path, project, workspace)
    assert "visible-control" in result.stdout, result.stdout + result.stderr  # the folder is reachable
    assert "TOKEN=real" not in result.stdout
    assert (workspace / "server.pem").read_text(encoding="utf-8") == "pem"
