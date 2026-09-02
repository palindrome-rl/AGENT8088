"""Install the reviewed text-to-cad CAD Viewer runtime.

Only the loopback Python server and prebuilt browser application are extracted.
The archive is pinned by commit and SHA-256 so installer execution cannot drift
with upstream ``main``. Heavy OpenCascade work stays in Agent8088's isolated
CAD environment; the Viewer never installs or executes npm dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

VIEWER_VERSION = "0.4.28"
VIEWER_COMMIT = "0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6"
VIEWER_ARCHIVE_SHA256 = "8a349d4287407c79392e736c9d2e2d9c52e0427a58d168a4f325f926dfd7b7d1"
VIEWER_ARCHIVE_URL = (
    "https://codeload.github.com/earthtojake/text-to-cad/zip/" + VIEWER_COMMIT
)
_ARCHIVE_LIMIT = 64 * 1024 * 1024
_EXTRACTED_LIMIT = 48 * 1024 * 1024
_VIEWER_PREFIX = "skills/cad-viewer/scripts/viewer/"
_REQUIRED = ("LICENSE", "dist/index.html", "server_py/server.py", "server_py/start_viewer.py")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(destination: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                VIEWER_ARCHIVE_URL,
                headers={"User-Agent": "Agent8088-CAD-Viewer-Installer/1"},
            )
            with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _ARCHIVE_LIMIT:
                        raise RuntimeError("CAD Viewer archive exceeds the 64 MB safety limit")
                    out.write(chunk)
            return
        except (OSError, RuntimeError, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1 << attempt)
    raise RuntimeError(f"CAD Viewer download failed after three attempts: {last_error}")


def _member_relative_path(name: str) -> Path | None:
    parts = PurePosixPath(name).parts
    try:
        skills_index = parts.index("skills")
    except ValueError:
        return None
    relative = PurePosixPath(*parts[skills_index:]).as_posix()
    if relative == "skills/cad-viewer/LICENSE":
        return Path("LICENSE")
    if not relative.startswith(_VIEWER_PREFIX):
        return None
    viewer_relative = PurePosixPath(relative[len(_VIEWER_PREFIX):])
    if not viewer_relative.parts:
        return None
    if viewer_relative.parts[0] not in {"dist", "server_py"}:
        return None
    if any(part in {"", ".", ".."} for part in viewer_relative.parts):
        raise RuntimeError(f"unsafe CAD Viewer archive member: {name}")
    return Path(*viewer_relative.parts)


def _write_manifest(target: Path, release_root: Path) -> dict[str, str]:
    manifest = {
        "version": VIEWER_VERSION,
        "commit": VIEWER_COMMIT,
        "sha256": VIEWER_ARCHIVE_SHA256,
        "root": str(release_root),
    }
    manifest_path = target / "current.json"
    temporary = manifest_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
    return manifest


def install_viewer(target: Path, archive: Path | None = None) -> dict[str, str]:
    target = target.resolve()
    release_root = target / "releases" / VIEWER_COMMIT
    target.mkdir(parents=True, exist_ok=True)
    if all((release_root / item).is_file() for item in _REQUIRED):
        return _write_manifest(target, release_root)

    with tempfile.TemporaryDirectory(prefix="agent8088-viewer-download-") as temp_dir:
        archive_path = Path(archive).resolve() if archive else Path(temp_dir) / "viewer.zip"
        if not archive:
            _download(archive_path)
        if archive_path.stat().st_size > _ARCHIVE_LIMIT:
            raise RuntimeError("CAD Viewer archive exceeds the 64 MB safety limit")
        if _sha256(archive_path).lower() != VIEWER_ARCHIVE_SHA256:
            raise RuntimeError(
                "CAD Viewer archive checksum mismatch; refusing unreviewed upstream content"
            )

        staging = target / "releases" / f".{VIEWER_COMMIT}.{os.getpid()}.staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        extracted = 0
        extracted_bytes = 0
        destinations: set[Path] = set()
        try:
            with zipfile.ZipFile(archive_path) as bundle:
                for member in bundle.infolist():
                    relative = _member_relative_path(member.filename)
                    if relative is None or member.is_dir():
                        continue
                    if relative in destinations:
                        raise RuntimeError(f"duplicate CAD Viewer archive member: {relative}")
                    destinations.add(relative)
                    extracted_bytes += int(member.file_size)
                    if extracted_bytes > _EXTRACTED_LIMIT:
                        raise RuntimeError("CAD Viewer runtime exceeds the 48 MB extraction limit")
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    extracted += 1
            missing = [item for item in _REQUIRED if not (staging / item).is_file()]
            if missing:
                raise RuntimeError("CAD Viewer archive is incomplete: " + ", ".join(missing))
            if extracted < 10:
                raise RuntimeError("CAD Viewer archive contained too few runtime files")
            release_root.parent.mkdir(parents=True, exist_ok=True)
            if release_root.exists():
                shutil.rmtree(release_root)
            os.replace(staging, release_root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return _write_manifest(target, release_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        result = install_viewer(args.target, args.archive)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
