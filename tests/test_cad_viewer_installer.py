"""Security and lifecycle checks for the pinned CAD Viewer installer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install_cad_viewer", ROOT / "scripts" / "install_cad_viewer.py"
)
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def _archive(path: Path, *, unsafe: str | None = None) -> str:
    prefix = "text-to-cad-test/skills/cad-viewer/scripts/viewer/"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("text-to-cad-test/skills/cad-viewer/LICENSE", "MIT\n")
        bundle.writestr(prefix + "dist/index.html", "<html>viewer</html>")
        bundle.writestr(prefix + "server_py/server.py", "APP = 'cad-viewer'\n")
        bundle.writestr(prefix + "server_py/start_viewer.py", "pass\n")
        for index in range(8):
            bundle.writestr(prefix + f"dist/assets/runtime-{index}.js", "// runtime\n")
        bundle.writestr(prefix + "package.json", "{\"scripts\": {\"postinstall\": \"bad\"}}")
        if unsafe:
            bundle.writestr(prefix + unsafe, "bad")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_installs_only_reviewed_runtime_and_writes_atomic_manifest(tmp_path, monkeypatch):
    archive = tmp_path / "viewer.zip"
    digest = _archive(archive)
    monkeypatch.setattr(INSTALLER, "VIEWER_ARCHIVE_SHA256", digest)
    target = tmp_path / "managed-viewer"

    manifest = INSTALLER.install_viewer(target, archive)
    release = Path(manifest["root"])

    assert (release / "LICENSE").read_text() == "MIT\n"
    assert (release / "dist/index.html").is_file()
    assert (release / "server_py/server.py").is_file()
    assert not (release / "package.json").exists()
    assert json.loads((target / "current.json").read_text())["root"] == str(release)
    assert not list((target / "releases").glob("*.staging"))


def test_refuses_archive_with_wrong_checksum(tmp_path):
    archive = tmp_path / "viewer.zip"
    _archive(archive)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        INSTALLER.install_viewer(tmp_path / "target", archive)


def test_rejects_traversal_inside_selected_runtime(tmp_path, monkeypatch):
    archive = tmp_path / "viewer.zip"
    digest = _archive(archive, unsafe="dist/../escape.py")
    monkeypatch.setattr(INSTALLER, "VIEWER_ARCHIVE_SHA256", digest)
    with pytest.raises(RuntimeError, match="unsafe CAD Viewer archive member"):
        INSTALLER.install_viewer(tmp_path / "target", archive)


def test_existing_complete_release_is_reused_without_archive(tmp_path):
    target = tmp_path / "managed-viewer"
    release = target / "releases" / INSTALLER.VIEWER_COMMIT
    for relative in INSTALLER._REQUIRED:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")

    manifest = INSTALLER.install_viewer(target, tmp_path / "does-not-exist.zip")

    assert Path(manifest["root"]) == release
