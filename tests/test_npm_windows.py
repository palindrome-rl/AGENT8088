"""A node_modules installed by npm in the WSL sandbox (on Linux) made usable on Windows: the Windows platform
packages added, files an install script replaced put back, .cmd/.ps1 shims written, native addons reported."""
import json
from pathlib import Path

import pytest

from agent8088 import npm_windows as N


def pkg(root: Path, rel: str, manifest: dict, files: dict | None = None) -> Path:
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, data in (files or {}).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return folder


LAUNCHER = "#!/usr/bin/env node\nrequire('../lib/main.js')\n"


@pytest.fixture
def trees(tmp_path):
    """The same project as npm leaves it on Linux (scripts run) and as it is fetched for Windows (no scripts)."""
    linux, windows = tmp_path / "project" / "node_modules", tmp_path / "win" / "node_modules"
    for root, binary in ((linux, b"\x7fELF-linux-binary"), (windows, LAUNCHER.encode())):
        pkg(root, "esbuild", {"name": "esbuild", "bin": {"esbuild": "bin/esbuild"},
                              "scripts": {"postinstall": "node install.js"},
                              "optionalDependencies": {"@esbuild/linux-x64": "1", "@esbuild/win32-x64": "1"}},
            {"bin/esbuild": binary, "lib/main.js": "module.exports = 1\n"})
        pkg(root, "dayjs", {"name": "dayjs"}, {"index.js": "x"})
        pkg(root, "vite", {"name": "vite", "bin": "bin/vite.js"}, {"bin/vite.js": LAUNCHER})
    pkg(linux, "@esbuild/linux-x64", {"name": "@esbuild/linux-x64", "os": ["linux"], "cpu": ["x64"]}, {"bin/esbuild": b"\x7fELF"})
    pkg(windows, "@esbuild/win32-x64", {"name": "@esbuild/win32-x64", "os": ["win32"], "cpu": ["x64"]},
        {"esbuild.exe": b"MZ-windows"})
    (linux / ".bin").mkdir()
    (linux / ".bin" / "esbuild").write_bytes(b"")   # how a Linux symlink looks from Windows
    return linux, windows


@pytest.mark.parametrize("command", ["npm install", "npm i esbuild", "cd app && npm ci", "npm --prefix x install",
                                     "npm install --save-dev esbuild && npm run build", "npm.cmd install", "npm update"])
def test_npm_installs_are_recognised(command):
    assert N.is_npm_install(command)


@pytest.mark.parametrize("command", ["npm run build", "npm test", "npx esbuild", "pnpm install", "echo npm install-ish",
                                     "node install.js"])
def test_other_commands_are_not(command):
    assert not N.is_npm_install(command)


def test_a_linux_tree_is_recognised_by_its_platform_packages(trees):
    linux, windows = trees
    assert N.linux_only(linux) == ["@esbuild/linux-x64"]
    assert N.linux_only(windows) == []


def test_the_windows_platform_package_is_added_next_to_the_linux_one(trees):
    linux, windows = trees
    result = N.merge_windows_tree(linux, windows)
    assert result.added == ["@esbuild/win32-x64"]
    assert (linux / "@esbuild" / "win32-x64" / "esbuild.exe").read_bytes() == b"MZ-windows"
    assert (linux / "@esbuild" / "linux-x64" / "bin" / "esbuild").is_file()   # Linux keeps working


def test_a_file_an_install_script_replaced_is_put_back(trees):
    linux, windows = trees
    result = N.merge_windows_tree(linux, windows)
    assert "esbuild" in result.restored
    assert (linux / "esbuild" / "bin" / "esbuild").read_text() == LAUNCHER   # the launcher, not the ELF
    assert "dayjs" not in result.restored   # no install script: never touched


def test_a_hard_link_to_the_linux_binary_is_broken_not_written_through(trees):
    """esbuild's Linux install links bin/esbuild to the platform package's binary: one file, two names."""
    linux, windows = trees
    binary = linux / "@esbuild" / "linux-x64" / "bin" / "esbuild"
    launcher = linux / "esbuild" / "bin" / "esbuild"
    launcher.unlink()
    try:
        import os
        os.link(binary, launcher)
    except OSError as exc:
        pytest.skip(f"no hard links here: {exc}")
    N.merge_windows_tree(linux, windows)
    assert launcher.read_text() == LAUNCHER
    assert binary.read_bytes() == b"ELF"   # the Linux binary is untouched


def test_windows_shims_are_written_for_every_tool(trees):
    linux, windows = trees
    result = N.merge_windows_tree(linux, windows)
    assert result.shims == ["esbuild", "vite"]
    cmd = (linux / ".bin" / "esbuild.cmd").read_bytes().decode()
    assert r'"%~dp0\..\esbuild\bin\esbuild"' in cmd and "\r\n" in cmd
    assert '"$PSScriptRoot/../vite/bin/vite.js"' in (linux / ".bin" / "vite.ps1").read_text()


def test_no_shim_for_a_native_executable_or_a_path_out_of_node_modules(tmp_path):
    modules = tmp_path / "node_modules"
    pkg(modules, "tool", {"name": "tool", "bin": {"tool": "bin/tool", "escape": "../../outside.js"}},
        {"bin/tool": b"\x7fELF"})
    (tmp_path / "outside.js").write_text(LAUNCHER)
    assert N.write_shims(modules) == []


def test_a_native_addon_is_reported(trees):
    linux, windows = trees
    pkg(linux, "bcrypt", {"name": "bcrypt", "gypfile": True}, {"binding.gyp": "{}"})
    result = N.merge_windows_tree(linux, windows)
    assert result.native == ["bcrypt"]
    assert "npm install` on Windows" in result.note()


def test_the_note_names_what_was_added(trees):
    note = N.merge_windows_tree(*trees).note()
    assert "@esbuild/win32-x64" in note and "WSL sandbox" in note


def test_nothing_to_note_for_an_empty_tree(tmp_path):
    (tmp_path / "node_modules").mkdir()
    assert N.merge_windows_tree(tmp_path / "node_modules", tmp_path / "node_modules").note() == ""


def test_the_engine_adds_windows_builds_after_an_npm_install_in_wsl(engine, tmp_path, monkeypatch):
    """The glue: after `npm install` in the WSL sandbox the engine fetches the Windows tree and merges it."""
    project = tmp_path / "project"
    linux = project / "node_modules"
    pkg(linux, "@esbuild/linux-x64", {"name": "@esbuild/linux-x64", "os": ["linux"]})
    (project / "package.json").write_text('{"name": "p"}')
    data = tmp_path / "data"
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: data)
    monkeypatch.setattr(engine, "_wsl_sandbox_ready", lambda *a, **k: True)
    calls = []

    def fake_exec(command, timeout, cwd=None, readonly=False, service=""):
        calls.append((command, cwd))
        if "--os=win32" in command:
            pkg(Path(cwd) / "node_modules", "@esbuild/win32-x64", {"name": "@esbuild/win32-x64", "os": ["win32"]},
                {"esbuild.exe": b"MZ"})
        return "added 3 packages"
    monkeypatch.setattr(engine, "_exec_wsl_sandbox", fake_exec)
    out = engine._run_in_sandbox_backend("wsl", "npm install", 60, False, "", project)
    assert "@esbuild/win32-x64" in out
    assert (linux / "@esbuild" / "win32-x64" / "esbuild.exe").is_file()
    second = [c for c in calls if "--os=win32" in c[0]]
    assert second and "--ignore-scripts" in second[0][0]
    assert not list((data / "sandbox-tmp").glob("npm-windows-*"))   # the temporary copy is removed


def test_the_engine_leaves_a_failed_install_alone(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_wsl_sandbox_ready", lambda *a, **k: True)
    monkeypatch.setattr(engine, "_exec_wsl_sandbox", lambda *a, **k: "npm ERR!\nCommand exited with status 1.")
    monkeypatch.setattr(engine, "_wsl_windows_node_modules", lambda *a: pytest.fail("ran after a failed install"))
    assert "status 1" in engine._run_in_sandbox_backend("wsl", "npm install", 60, False, "", tmp_path)
