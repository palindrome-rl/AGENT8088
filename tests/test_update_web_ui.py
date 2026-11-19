"""--update rebuilds the Web UI and keeps a complete old bundle on failure."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

from agent8088 import cli


def _install(tmp_path, lock=True):
    web = tmp_path / "install" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text("{}", encoding="utf-8")
    if lock:
        (web / "package-lock.json").write_text("{}", encoding="utf-8")
    return tmp_path / "install", tmp_path / "home"


def _write_bundle(out_dir, script="/assets/app.js"):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        f'<html><head><link rel="stylesheet" href="/assets/app.css"></head>'
        f'<body><script type="module" src="{script}"></script></body></html>',
        encoding="utf-8",
    )
    assets = out_dir / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "app.js").write_text("console.log('new')", encoding="utf-8")
    (assets / "app.css").write_text("body{}", encoding="utf-8")


def _successful_runner(seen=None):
    def runner(argv, cwd, env):
        if seen is not None:
            seen.append((list(argv), cwd, dict(env)))
        if "--outDir" in argv:
            _write_bundle(Path(argv[argv.index("--outDir") + 1]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return runner


def test_installs_with_build_dependencies_then_builds_in_staging(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    monkeypatch.setattr(cli, "_find_npm", lambda h: str(tmp_path / "node with spaces" / "npm"))
    monkeypatch.setenv("NODE_ENV", "production")
    seen = []
    ok, msg = cli._rebuild_web_ui(install, home, runner=_successful_runner(seen))

    assert ok and "verified" in msg
    assert seen[0][0][1:] == ["ci", "--include=dev", "--include=optional", "--no-audit", "--no-fund"]
    assert seen[0][2]["NODE_ENV"] == "production"
    assert "--outDir" in seen[1][0]
    assert (install / "web" / "dist" / "index.html").is_file()
    assert (install / "web" / "dist" / "assets" / "app.js").is_file()
    assert not list((install / "web").glob(".agent8088-web-build-*"))


def test_failed_build_leaves_previous_bundle_intact(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    dist = install / "web" / "dist"
    _write_bundle(dist, script="/assets/old.js")
    (dist / "assets" / "old.js").write_text("old", encoding="utf-8")
    old_index = (dist / "index.html").read_bytes()
    monkeypatch.setattr(cli, "_find_npm", lambda h: "/x/npm")

    def runner(argv, cwd, env):
        if "--outDir" in argv:
            stage_dist = Path(argv[argv.index("--outDir") + 1])
            _write_bundle(stage_dist)
            return SimpleNamespace(returncode=1, stdout="", stderr="vite: boom")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ok, msg = cli._rebuild_web_ui(install, home, runner=runner)
    assert not ok and "vite: boom" in msg and "left untouched" in msg
    assert (dist / "index.html").read_bytes() == old_index
    assert (dist / "assets" / "old.js").read_text(encoding="utf-8") == "old"
    assert not list((install / "web").glob(".agent8088-web-build-*"))


def test_missing_referenced_asset_cannot_replace_old_bundle(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    dist = install / "web" / "dist"
    _write_bundle(dist, script="/assets/old.js")
    (dist / "assets" / "old.js").write_text("old", encoding="utf-8")
    old_index = (dist / "index.html").read_bytes()
    monkeypatch.setattr(cli, "_find_npm", lambda h: "/x/npm")

    def runner(argv, cwd, env):
        if "--outDir" in argv:
            _write_bundle(Path(argv[argv.index("--outDir") + 1]), script="/assets/missing.js")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ok, msg = cli._rebuild_web_ui(install, home, runner=runner)
    assert not ok and "missing or empty local assets" in msg
    assert (dist / "index.html").read_bytes() == old_index
    assert (dist / "assets" / "old.js").is_file()


@pytest.mark.parametrize("empty_file", ["index.html", "assets/app.js"])
def test_empty_bundle_files_cannot_replace_old_bundle(tmp_path, monkeypatch, empty_file):
    install, home = _install(tmp_path)
    dist = install / "web" / "dist"
    _write_bundle(dist)
    original = (dist / "index.html").read_bytes()
    monkeypatch.setattr(cli, "_find_npm", lambda h: "/x/npm")

    def runner(argv, cwd, env):
        if "--outDir" in argv:
            output = Path(argv[argv.index("--outDir") + 1])
            _write_bundle(output)
            (output / empty_file).write_text("", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ok, message = cli._rebuild_web_ui(install, home, runner=runner)
    assert not ok and "NOT updated" in message
    assert (dist / "index.html").read_bytes() == original
    assert not list((install / "web").glob(".agent8088-web-build-*"))


def test_activation_failure_restores_old_bundle(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    dist = install / "web" / "dist"
    _write_bundle(dist, script="/assets/old.js")
    (dist / "assets" / "old.js").write_text("old", encoding="utf-8")
    old_index = (dist / "index.html").read_bytes()
    monkeypatch.setattr(cli, "_find_npm", lambda h: "/x/npm")
    _real_rename = Path.rename

    def fail_activation(path, target):
        if path.name == "dist" and path.parent.name.startswith(".agent8088-web-build-"):
            raise OSError("simulated activation failure")
        return _real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_activation)
    ok, msg = cli._rebuild_web_ui(install, home, runner=_successful_runner())
    assert not ok and "simulated activation failure" in msg
    assert (dist / "index.html").read_bytes() == old_index
    assert (dist / "assets" / "old.js").is_file()
    assert not list((install / "web").glob(".agent8088-web-build-*"))


def test_failed_restore_retains_previous_bundle_in_staging(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    dist = install / "web" / "dist"
    _write_bundle(dist, script="/assets/old.js")
    (dist / "assets" / "old.js").write_text("old", encoding="utf-8")
    old_index = (dist / "index.html").read_bytes()
    monkeypatch.setattr(cli, "_find_npm", lambda h: "/x/npm")
    _real_rename = Path.rename

    def fail_both(path, target):
        if path.name == "dist" and path.parent.name.startswith(".agent8088-web-build-"):
            raise OSError("simulated activation failure")
        if path.name == "previous-dist":
            raise OSError("simulated restore failure")
        return _real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_both)
    ok, msg = cli._rebuild_web_ui(install, home, runner=_successful_runner())
    assert not ok and "previous build is preserved" in msg
    backup = next((install / "web").glob(".agent8088-web-build-*/previous-dist"))
    assert (backup / "index.html").read_bytes() == old_index
    assert (backup / "assets" / "old.js").is_file()


def test_missing_node_is_a_clear_failure(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    monkeypatch.setattr(cli, "_find_npm", lambda h: None)
    ok, msg = cli._rebuild_web_ui(install, home)
    assert not ok and "Node.js" in msg


def test_install_without_web_source_is_not_an_error(tmp_path):
    ok, _ = cli._rebuild_web_ui(tmp_path, tmp_path)
    assert ok


def test_run_update_rebuilds_even_when_head_did_not_change(tmp_path, monkeypatch):
    home = tmp_path / "home"
    install = home / "agent8088"
    (install / "web" / "dist").mkdir(parents=True)
    (install / "web" / "dist" / "index.html").write_text("stale", encoding="utf-8")
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_resolve_update_branch", lambda _: ("development", ""))
    monkeypatch.setattr(sys, "argv", ["python"])
    git_calls = []

    def fake_git(directory, *args):
        git_calls.append(args)
        if args == ("status", "--porcelain"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args == ("rev-parse", "--short", "HEAD"):
            return SimpleNamespace(returncode=0, stdout="same123", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    rebuilt = []
    monkeypatch.setattr(cli, "_git", fake_git)
    monkeypatch.setattr(cli, "_rebuild_web_ui", lambda i, h: rebuilt.append((i, h)) or (True, "rebuilt"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    assert cli._run_update() is True
    assert len(rebuilt) == 1 and rebuilt[0][0] == install
    assert ("fetch", "--depth", "1", "origin", "development") in git_calls
