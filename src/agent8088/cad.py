"""CAD runtime plumbing: the isolated build123d/cadgen venv, the Viewer, and
the generic .step/.stp read fallback.

Generation itself is no longer a bespoke tool surface here -- the model
drives the vendored earthtojake/text-to-cad skill (skills_installed/cad/)
directly via execute_shell/write_file, the same way any coding agent that
installs that skill would. This module only keeps what still has to be
engine-internal, trusted code:

* build123d/cadgen runtime + Viewer install/health/launch (general plumbing,
  independent of how generation is driven);
* extract_info(), the read_text fallback for .step/.stp files -- it shells
  out to the vendored skill's own scripts/inspect, the same command the
  model would run, so this is not a second implementation to keep in sync.

The main Agent8088 environment never imports build123d/cadgen directly.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from .documents import _readable_or_reason

MAX_CAD_BYTES = 200 * 1024 * 1024

CADGEN_VERSION = "0.4.28"
BUILD123D_VERSION = "0.11.1"
CAD_VIEWER_VERSION = "0.4.28"
CAD_VIEWER_COMMIT = "0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6"
CAD_VIEWER_SHA256 = "8a349d4287407c79392e736c9d2e2d9c52e0427a58d168a4f325f926dfd7b7d1"

CAD_EXTENSIONS = (
    ".step", ".stp", ".stl", ".3mf", ".glb", ".brep",
)


def _agent_home() -> Path:
    configured = os.environ.get("AGENT8088_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "agent8088"
    return Path.home() / ".agent8088"


def cad_runtime_root() -> Path:
    override = os.environ.get("AGENT8088_CAD_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return _agent_home() / "integrations" / "cad"


def cad_runtime_python() -> Path:
    override = os.environ.get("AGENT8088_CAD_PYTHON", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    root = cad_runtime_root() / "venv"
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def cad_viewer_root() -> Path:
    """Resolve the checksum-pinned text-to-cad Viewer release."""
    override = os.environ.get("AGENT8088_CAD_VIEWER_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    manifest = cad_runtime_root() / "viewer" / "current.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        root = Path(str(payload.get("root") or "")).resolve(strict=False)
        releases = (manifest.parent / "releases").resolve(strict=False)
        root.relative_to(releases)
        if (
            str(payload.get("version")) == CAD_VIEWER_VERSION
            and str(payload.get("commit")) == CAD_VIEWER_COMMIT
            and str(payload.get("sha256")) == CAD_VIEWER_SHA256
            and root.name == CAD_VIEWER_COMMIT
        ):
            return root
    except (OSError, ValueError, TypeError):
        pass
    return cad_runtime_root() / "viewer" / "missing"


def cad_viewer_status() -> dict[str, Any]:
    root = cad_viewer_root()
    required = (
        root / "LICENSE",
        root / "dist" / "index.html",
        root / "server_py" / "server.py",
        root / "server_py" / "start_viewer.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    return {
        "available": not missing,
        "version": CAD_VIEWER_VERSION,
        "root": str(root),
        "missing": missing,
    }


def cad_runtime_status(timeout: int = 45) -> dict[str, Any]:
    """Return a real import probe for the isolated CAD runtime."""
    python = cad_runtime_python()
    result: dict[str, Any] = {
        "available": False,
        "python": str(python),
        "root": str(cad_runtime_root()),
        "cadgen": CADGEN_VERSION,
        "build123d": BUILD123D_VERSION,
        "viewer": cad_viewer_status(),
    }
    if not python.is_file():
        result["reason"] = "runtime interpreter is missing"
        return result
    code = (
        "from importlib.metadata import version; "
        "import build123d, cadgen; "
        "print(version('build123d') + '|' + version('cadgen'))"
    )
    try:
        done = subprocess.run(
            [str(python), "-I", "-c", code], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=max(1, int(timeout)),
            shell=False, env=_worker_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"runtime probe failed: {exc}"
        return result
    versions = (done.stdout or "").strip().split("|")
    if done.returncode == 0 and versions == [BUILD123D_VERSION, CADGEN_VERSION]:
        result["available"] = bool(result["viewer"]["available"])
        result["installed_versions"] = versions
        if not result["available"]:
            result["reason"] = "CAD Viewer runtime is missing or incomplete"
    else:
        detail = (done.stderr or done.stdout or f"exit {done.returncode}").strip()
        result["reason"] = detail[:500]
    return result


_NOT_INSTALLED_MESSAGE = (
    "Agent8088's advanced CAD runtime is not installed, so this CAD operation "
    "cannot run. Re-run the Agent8088 installer to install the pinned build123d "
    f"{BUILD123D_VERSION} + text-to-cad cadgen {CADGEN_VERSION} runtime."
)


def _worker_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_agent_home() / "playwright-browsers"))
    return env


def _viewer_server_info(port: int, workspace: Path | None = None,
                        timeout: float = 1.0) -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{int(port)}/__cad/server",
            headers={"User-Agent": "Agent8088-CAD-Viewer/1"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        if isinstance(payload, dict) and payload.get("app") == "cad-viewer":
            if workspace is not None:
                served = Path(str(payload.get("rootDir") or "")).resolve(strict=False)
                if os.path.normcase(str(served)) != os.path.normcase(str(workspace.resolve())):
                    return None
            return payload
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return None


def _port_is_bindable(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False


def _viewer_url(workspace: Path, path: Path, port: int) -> str:
    directory = workspace.as_posix()
    if os.name == "nt" and not directory.startswith("/"):
        directory = "/" + directory
    url_path = urllib.parse.quote(directory, safe="/:")
    relative = path.relative_to(workspace).as_posix()
    return f"http://127.0.0.1:{port}{url_path}?" + urllib.parse.urlencode({"file": relative})


def open_cad_viewer(path, workspace=None, launch_browser: bool = True,
                    timeout: int = 45) -> str:
    """Start or reuse the managed loopback Viewer and open one CAD artifact."""
    path = Path(path).expanduser().resolve(strict=False)
    if not path.is_file():
        return f"Cannot open CAD Viewer: {path} does not exist."
    supported = {".step", ".stp", ".stl", ".3mf", ".glb", ".dxf"}
    if path.suffix.lower() not in supported:
        return "Cannot open CAD Viewer: unsupported file type " + path.suffix.lower()
    root = Path(workspace).expanduser().resolve(strict=False) if workspace else path.parent
    try:
        path.relative_to(root)
    except ValueError:
        return "Cannot open CAD Viewer: the artifact is outside the authorized workspace."
    viewer = cad_viewer_status()
    if not viewer["available"]:
        return (
            "CAD Viewer is not installed. Re-run the Agent8088 installer to install "
            f"the pinned text-to-cad Viewer {CAD_VIEWER_VERSION} runtime."
        )
    python = cad_runtime_python()
    if not python.is_file():
        return _NOT_INSTALLED_MESSAGE

    selected_port = next(
        (port for port in range(3245, 3256) if _viewer_server_info(port, root)),
        None,
    )
    # Only look for a new port after checking the whole managed range for an
    # existing verified Viewer. This avoids spawning duplicate servers merely
    # because an earlier port happens to be free.
    if selected_port is None:
        selected_port = next(
            (port for port in range(3245, 3256) if _port_is_bindable(port)),
            None,
        )
    if selected_port is None:
        return "Could not start CAD Viewer: ports 3245-3255 are unavailable."

    if _viewer_server_info(selected_port, root) is None:
        viewer_root = Path(viewer["root"])
        state_root = cad_runtime_root() / "viewer" / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        log_path = state_root / f"viewer-{selected_port}.log"
        env = _worker_env()
        env["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(viewer_root), env.get("PYTHONPATH", "")) if item
        )
        command = [
            str(python), "-m", "server_py.server", "--host", "127.0.0.1",
            "--port", str(selected_port),
        ]
        process_options: dict[str, Any]
        if os.name == "nt":
            process_options = {
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            }
        else:
            process_options = {"start_new_session": True}
        try:
            with log_path.open("ab", buffering=0) as log:
                subprocess.Popen(
                    command, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                    **process_options,
                )
        except OSError as exc:
            return f"Could not start CAD Viewer: {exc}"

        deadline = time.monotonic() + max(3, int(timeout))
        while time.monotonic() < deadline:
            if _viewer_server_info(selected_port, root):
                break
            time.sleep(0.2)
        else:
            try:
                detail = log_path.read_text(encoding="utf-8", errors="replace")[-1000:].strip()
            except OSError:
                detail = ""
            return "CAD Viewer did not become ready." + (f" Log: {detail}" if detail else "")

    url = _viewer_url(root, path, selected_port)
    opened = False
    if launch_browser:
        try:
            opened = bool(webbrowser.open(url, new=2))
        except webbrowser.Error:
            opened = False
    action = "Opened" if opened else "CAD Viewer ready"
    return f"{action}: {url}"


def _cad_skill_scripts_dir() -> Path:
    # Mirrors engine.py's own _cad_skill_scripts_dir() default (skills_dir
    # config defaults to this same path); a reconfigured skills_dir only
    # affects this read-fallback probe, not the CAD-scoped shell auto-approval
    # gate, which is the actual security boundary.
    return Path(__file__).with_name("skills_installed") / "cad" / "scripts"


def extract_info(path, max_bytes: int = MAX_CAD_BYTES):
    """Return a deterministic geometry summary, or None for a non-CAD file.

    Shells out to the vendored text-to-cad skill's own scripts/inspect --
    the same command the model runs -- rather than a second implementation.
    """
    path = Path(path)
    if path.suffix.lower() not in CAD_EXTENSIONS:
        return None
    if not path.exists():
        return f"Cannot inspect: {path} does not exist."
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"CAD file is too large to inspect (limit: {max_bytes} bytes): {path}")
    unreadable = _readable_or_reason(path)
    if unreadable:
        return unreadable
    python = cad_runtime_python()
    inspect_script = _cad_skill_scripts_dir() / "inspect"
    if not python.is_file() or not inspect_script.is_file():
        return _NOT_INSTALLED_MESSAGE
    # Upstream's inspect CLI resolves its target relative to cwd -- run it
    # from the file's own directory with a bare filename, as documented.
    try:
        done = subprocess.run(
            [str(python), str(inspect_script), "refs", path.name, "--facts"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, shell=False, cwd=str(path.parent), env=_worker_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Could not inspect {path.name}: inspect timed out after 180s."
    except OSError as exc:
        return f"Could not inspect {path.name}: {exc}"
    try:
        payload = json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        detail = (done.stderr or done.stdout or f"exited {done.returncode}").strip()
        return f"Could not inspect {path.name}: {detail[:500]}"
    tokens = payload.get("tokens") or []
    if not payload.get("ok") or not tokens:
        errors = payload.get("errors") or []
        detail = errors[0].get("message") if errors else "unknown CAD error"
        return f"Could not inspect {path.name}: {detail}"
    summary = tokens[0].get("summary") or {}
    lines = [f"CAD file: {path.name}"]
    for key in ("kind", "occurrenceCount", "leafOccurrenceCount", "shapeCount",
                "faceCount", "edgeCount", "vertexCount"):
        if key in summary:
            lines.append(f"{key}: {summary[key]}")
    bounds = summary.get("bounds") or {}
    if bounds:
        lines.append(f"bounds: min={bounds.get('min')} max={bounds.get('max')}")
    return "\n".join(lines)
