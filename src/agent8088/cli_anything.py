"""Isolated, structured integration with HKUDS CLI-Anything's CLI-Hub.

Agent8088 remains the orchestrator.  This module manages the optional CLI-Hub
runtime in a separate virtual environment and never executes model-authored
shell strings.  Registry data is untrusted input, so automatic management is
limited to CLI-Anything's Python harness entries; public npm/uv/generic command
installers stay visible through search/info but require manual review.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CLI_HUB_PACKAGE = "cli-anything-hub"
CLI_HUB_VERSION = "0.4.1"
CLI_ANYTHING_REVISION = "810c18b0d1ab9b234bc996c9fd999318523a3ef0"
CLI_HUB_REGISTRY = "https://hkuds.github.io/CLI-Anything/registry.json"
TRUSTED_HARNESS_INSTALL_PREFIX = (
    "pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory="
)
MAX_NAME_LENGTH = 80
MAX_ARGUMENTS = 128
MAX_ARGUMENT_LENGTH = 16_384
MAX_SKILL_BYTES = 512 * 1024


def integration_root(config_path: Path | str) -> Path:
    override = os.environ.get("AGENT8088_CLI_ANYTHING_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return Path(config_path).expanduser().resolve(strict=False).parent / "integrations" / "cli-anything"


def venv_dir(root: Path) -> Path:
    return root / "venv"


def venv_python(root: Path) -> Path:
    if os.name == "nt":
        return venv_dir(root) / "Scripts" / "python.exe"
    return venv_dir(root) / "bin" / "python"


def hub_executable(root: Path) -> Path:
    if os.name == "nt":
        return venv_dir(root) / "Scripts" / "cli-hub.exe"
    return venv_dir(root) / "bin" / "cli-hub"


def _state_home(root: Path) -> Path:
    return root / "state"


def _ledger_path(root: Path) -> Path:
    return _state_home(root) / ".cli-hub" / "installed.json"


def _load_ledger(root: Path) -> dict:
    path = _ledger_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_ledger(root: Path, value: dict) -> None:
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _managed_env(root: Path, *, include_venv_path: bool = True) -> dict[str, str]:
    env = dict(os.environ)
    real_home = env.get("HOME") or env.get("USERPROFILE")
    state = _state_home(root)
    state.mkdir(parents=True, exist_ok=True)
    env["CLI_HUB_NO_ANALYTICS"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_CACHE_DIR"] = str(root / "cache" / "pip")
    env["UV_CACHE_DIR"] = str(root / "cache" / "uv")
    env["XDG_CACHE_HOME"] = str(root / "cache")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # CLI-Hub currently stores its registry cache and install ledger below
    # Path.home(). Give it a private home without changing the real user home
    # for application harnesses executed later.
    env["HOME"] = str(state)
    # Keep user-level Git transport settings (for example, HTTP/1.1 on networks
    # that reset HTTP/2 transfers) while CLI-Hub itself keeps a private HOME.
    if real_home and not env.get("GIT_CONFIG_GLOBAL"):
        git_config = Path(real_home) / ".gitconfig"
        if git_config.is_file():
            env["GIT_CONFIG_GLOBAL"] = str(git_config)
    if os.name == "nt":
        env["USERPROFILE"] = str(state)
    if include_venv_path:
        bindir = hub_executable(root).parent
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return env


def _uv_executable(root: Path) -> Path | None:
    candidates = [
        root.parent.parent / "bin" / ("uv.exe" if os.name == "nt" else "uv"),
        Path(shutil.which("uv") or ""),
    ]
    return next((path for path in candidates if str(path) and path.is_file()), None)


def _freecad_macos_bin() -> Path | None:
    """Return FreeCAD's bundled headless CLI directory on macOS when present."""
    if sys.platform != "darwin":
        return None
    candidates = [
        Path("/Applications/FreeCAD.app/Contents/Resources/bin"),
        Path.home() / "Applications/FreeCAD.app/Contents/Resources/bin",
    ]
    return next((path for path in candidates if (path / "freecadcmd").is_file()), None)


def _run(argv: list[str], *, root: Path, timeout: int, cwd: Path | None = None,
         managed_home: bool = True, path_prefix: Path | None = None) -> subprocess.CompletedProcess:
    env = _managed_env(root) if managed_home else dict(os.environ)
    if not managed_home:
        env["CLI_HUB_NO_ANALYTICS"] = "1"
        env["PIP_CACHE_DIR"] = str(root / "cache" / "pip")
        env["UV_CACHE_DIR"] = str(root / "cache" / "uv")
        env["XDG_CACHE_HOME"] = str(root / "cache")
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PATH"] = str(hub_executable(root).parent) + os.pathsep + env.get("PATH", "")
    if path_prefix:
        env["PATH"] = str(path_prefix) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout)),
        shell=False,
        check=False,
    )


def _result(done: subprocess.CompletedProcess) -> str:
    output = "\n".join(part.strip() for part in (done.stdout, done.stderr) if part and part.strip())
    if done.returncode:
        output = (output + "\n" if output else "") + f"CLI-Anything exited with status {done.returncode}."
    return output or "CLI-Anything command completed."


def _safe_name(name: object) -> str:
    value = str(name or "").strip().lower()
    if not value or len(value) > MAX_NAME_LENGTH or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
        raise ValueError("CLI name must contain only letters, numbers, dots, underscores, or hyphens.")
    return value


def _safe_arguments(arguments: object) -> list[str]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("arguments must be a JSON array of strings.") from exc
    if not isinstance(arguments, list) or len(arguments) > MAX_ARGUMENTS:
        raise ValueError(f"arguments must be a JSON array with at most {MAX_ARGUMENTS} items.")
    out = []
    for item in arguments:
        if not isinstance(item, str):
            raise TypeError("every CLI argument must be a string.")
        if len(item) > MAX_ARGUMENT_LENGTH or any(char in item for char in ("\0", "\r", "\n")):
            raise ValueError("CLI arguments may not contain NUL or newline characters.")
        out.append(item)
    return out


def status(config_path: Path | str, *, timeout: int = 10) -> dict:
    root = integration_root(config_path)
    hub = hub_executable(root)
    result = {
        "available": False,
        "version": "",
        "expected_version": CLI_HUB_VERSION,
        "root": str(root),
        "executable": str(hub),
    }
    if not hub.is_file():
        return result
    try:
        done = _run([str(hub), "--version"], root=root, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return result
    if done.returncode == 0:
        result["available"] = True
        match = re.search(r"(\d+(?:\.\d+)+)", done.stdout or "")
        result["version"] = match.group(1) if match else (done.stdout or "").strip()
    return result


def setup(config_path: Path | str, *, timeout: int = 300) -> str:
    root = integration_root(config_path)
    root.mkdir(parents=True, exist_ok=True)
    current = status(config_path)
    if current["available"] and current["version"] == CLI_HUB_VERSION:
        return f"CLI-Anything is ready (CLI-Hub {CLI_HUB_VERSION}) at {root}."

    uv = _uv_executable(root)
    python = venv_python(root)
    if not python.is_file():
        if uv:
            done = _run([str(uv), "venv", "--seed", "--python", sys.executable, str(venv_dir(root))],
                        root=root, timeout=timeout)
        else:
            done = _run([sys.executable, "-m", "venv", str(venv_dir(root))],
                        root=root, timeout=timeout)
        if done.returncode:
            return _result(done)

    package = f"{CLI_HUB_PACKAGE}=={CLI_HUB_VERSION}"
    if uv:
        done = _run([str(uv), "pip", "install", "--python", str(python), package],
                    root=root, timeout=timeout)
    else:
        done = _run([str(python), "-m", "pip", "install", package],
                    root=root, timeout=timeout)
    if done.returncode:
        return _result(done)
    current = status(config_path)
    if not current["available"]:
        return "CLI-Hub installation finished, but its executable could not be verified."
    return f"CLI-Anything is ready (CLI-Hub {current['version']}) at {root}."


def _require_hub(config_path: Path | str) -> tuple[Path, Path]:
    root = integration_root(config_path)
    hub = hub_executable(root)
    if not status(config_path)["available"]:
        raise RuntimeError("CLI-Anything is not set up. Run cli_anything_setup first.")
    return root, hub


def search(config_path: Path | str, query: object, *, timeout: int = 30) -> str:
    value = str(query or "").strip()
    if not value or len(value) > 500 or any(char in value for char in ("\0", "\r", "\n")):
        raise ValueError("search query must be one non-empty line of at most 500 characters.")
    root, hub = _require_hub(config_path)
    return _result(_run([str(hub), "search", value, "--json"], root=root, timeout=timeout))


def list_clis(config_path: Path | str, *, timeout: int = 30) -> str:
    """Return the official CLI-Hub catalog as machine-readable JSON."""
    root, hub = _require_hub(config_path)
    return _result(_run([str(hub), "list", "--json"], root=root, timeout=timeout))


def info(config_path: Path | str, name: object, *, timeout: int = 30) -> str:
    root, hub = _require_hub(config_path)
    return _result(_run([str(hub), "info", _safe_name(name)], root=root, timeout=timeout))


def _registry_entry(config_path: Path | str, name: str, *, timeout: int) -> dict:
    raw = search(config_path, name, timeout=timeout)
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CLI-Hub returned malformed registry data.") from exc
    for entry in entries if isinstance(entries, list) else []:
        if str(entry.get("name", "")).lower() == name:
            return entry
    raise RuntimeError(f"CLI '{name}' was not found in the CLI-Anything registry.")


def manage(config_path: Path | str, action: str, name: object, *, timeout: int = 300) -> str:
    action = str(action or "").strip().lower()
    if action not in {"install", "update", "uninstall"}:
        raise ValueError("action must be install, update, or uninstall.")
    safe_name = _safe_name(name)
    root, _hub = _require_hub(config_path)
    entry = _registry_entry(config_path, safe_name, timeout=min(timeout, 60))
    source = str(entry.get("_source", "harness")).lower()
    strategy = str(entry.get("install_strategy") or "pip").lower()
    if source != "harness" or strategy != "pip":
        raise RuntimeError(
            f"Automatic {action} is restricted to isolated Python harnesses. "
            f"'{safe_name}' uses source={source}, strategy={strategy}; review it manually."
        )
    install_cmd = str(entry.get("install_cmd") or "")
    if not install_cmd.startswith(TRUSTED_HARNESS_INSTALL_PREFIX):
        raise RuntimeError(
            f"Automatic {action} refused because '{safe_name}' does not use the "
            "approved HKUDS CLI-Anything repository install pattern. Review its "
            "registry source manually."
        )
    subdirectory = install_cmd.removeprefix(TRUSTED_HARNESS_INSTALL_PREFIX)
    if (not subdirectory or ".." in Path(subdirectory).parts
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", subdirectory)):
        raise RuntimeError("The CLI-Anything harness subdirectory is invalid.")
    entry_point = str(entry.get("entry_point") or f"cli-anything-{safe_name}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", entry_point):
        raise RuntimeError("The CLI-Anything harness entry point is invalid.")

    python = venv_python(root)
    package_name = f"cli-anything-{safe_name}"
    uv = _uv_executable(root)
    if action == "uninstall":
        argv = ([str(uv), "pip", "uninstall", "--python", str(python), package_name]
                if uv else [str(python), "-m", "pip", "uninstall", "-y", package_name])
    else:
        pinned = (
            "git+https://github.com/HKUDS/CLI-Anything.git@"
            f"{CLI_ANYTHING_REVISION}#subdirectory={subdirectory}"
        )
        argv = ([str(uv), "pip", "install", "--python", str(python)]
                if uv else [str(python), "-m", "pip", "install"])
        if action == "update":
            argv.extend(["--upgrade", "--force-reinstall"])
        argv.append(pinned)
    done = _run(argv, root=root, timeout=timeout)
    if done.returncode:
        return _result(done)

    ledger = _load_ledger(root)
    if action == "uninstall":
        ledger.pop(safe_name, None)
    else:
        ledger[safe_name] = {
            "version": str(entry.get("version") or "unknown"),
            "entry_point": entry_point,
            "dist_name": package_name,
            "source": "harness",
            "strategy": "pip",
            "upstream_revision": CLI_ANYTHING_REVISION,
            "subdirectory": subdirectory,
        }
    _save_ledger(root, ledger)
    verb = {"install": "Installed", "update": "Updated", "uninstall": "Uninstalled"}[action]
    return f"{verb} {safe_name} in Agent8088's isolated CLI-Anything environment."


def installed_skill(config_path: Path | str, name: object, *, timeout: int = 15) -> str:
    """Load an installed harness's packaged SKILL.md inside the managed venv."""
    safe_name = _safe_name(name)
    root, _hub = _require_hub(config_path)
    try:
        entry = _load_ledger(root)[safe_name]
        dist_name = str(entry.get("dist_name") or f"cli-anything-{safe_name}")
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"CLI '{safe_name}' is not installed by Agent8088's CLI-Anything environment."
        ) from exc
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", dist_name):
        raise RuntimeError("The installed CLI distribution name is invalid.")

    resolver = (
        "from importlib import metadata\n"
        "import sys\n"
        "dist = metadata.distribution(sys.argv[1])\n"
        "for item in dist.files or ():\n"
        "    value = str(item).replace('\\\\', '/')\n"
        "    if value.endswith('/skills/SKILL.md') or value.endswith('skills/SKILL.md'):\n"
        "        print(dist.locate_file(item).resolve())\n"
        "        break\n"
    )
    done = _run(
        [str(venv_python(root)), "-c", resolver, dist_name],
        root=root,
        timeout=timeout,
    )
    if done.returncode:
        raise RuntimeError(_result(done))
    candidates = [line.strip() for line in (done.stdout or "").splitlines() if line.strip()]
    if len(candidates) != 1:
        raise RuntimeError(f"Installed CLI '{safe_name}' does not provide a packaged SKILL.md.")
    skill_path = Path(candidates[0]).resolve(strict=False)
    managed_venv = venv_dir(root).resolve(strict=False)
    try:
        skill_path.relative_to(managed_venv)
    except ValueError as exc:
        raise RuntimeError("The installed CLI skill resolved outside the managed environment.") from exc
    if not skill_path.is_file():
        raise RuntimeError(f"Installed CLI skill is missing: {skill_path}")
    if skill_path.stat().st_size > MAX_SKILL_BYTES:
        raise RuntimeError("The installed CLI skill is too large to load safely.")
    return skill_path.read_text(encoding="utf-8")


def run(config_path: Path | str, name: object, arguments: object, cwd: Path | str,
        *, timeout: int = 120) -> str:
    safe_name = _safe_name(name)
    argv = _safe_arguments(arguments)
    root, _hub = _require_hub(config_path)
    ledger = _ledger_path(root)
    try:
        installed = json.loads(ledger.read_text(encoding="utf-8"))
        entry = installed[safe_name]
        executable_name = str(entry["entry_point"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"CLI '{safe_name}' is not installed by Agent8088's CLI-Anything environment.") from exc
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", executable_name):
        raise RuntimeError("The installed CLI entry point is invalid.")
    executable = hub_executable(root).parent / (executable_name + (".exe" if os.name == "nt" else ""))
    if not executable.is_file():
        raise RuntimeError(f"Installed CLI executable is missing: {executable}")
    workdir = Path(cwd).expanduser().resolve(strict=False)
    if not workdir.is_dir():
        raise ValueError(f"Working directory does not exist: {workdir}")
    # The application receives the user's real HOME/USERPROFILE; CLI-Hub's
    # private home is only for registry cache and install bookkeeping.
    path_prefix = _freecad_macos_bin() if safe_name == "freecad" else None
    return _result(_run([str(executable), *argv], root=root, timeout=timeout,
                        cwd=workdir, managed_home=False, path_prefix=path_prefix))
