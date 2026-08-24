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


def _patch_freecad_harness(root: Path) -> None:
    """Repair boolean export in the pinned FreeCAD harness."""
    candidates = [
        *venv_dir(root).glob(
            "lib/python*/site-packages/cli_anything/freecad/utils/freecad_macro_gen.py"
        ),
        venv_dir(root) / "Lib/site-packages/cli_anything/freecad/utils/freecad_macro_gen.py",
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        return
    source = path.read_text(encoding="utf-8")
    marker = "# Agent8088: materialize stored boolean parts"
    if marker in source:
        return
    old_booleans = '    boolean_ops = project.get("boolean_ops", [])'
    new_booleans = '''    boolean_ops = list(project.get("boolean_ops", []))
    # Agent8088: materialize stored boolean parts
    known_names = {op.get("name") for op in boolean_ops}
    for part in project.get("parts", []):
        if part.get("type") not in {"cut", "fuse", "common"}:
            continue
        params = part.get("params", {})
        base = _part_by_id(project, params.get("base_id"))
        tool = _part_by_id(project, params.get("tool_id"))
        if base and tool and part.get("name") not in known_names:
            boolean_ops.append({
                "type": part["type"], "name": part["name"],
                "base": base["name"], "tool": tool["name"],
            })'''
    old_export = '''    lines.append("# Collect all shape objects for export")
    lines.append("export_objects = []")
    lines.append("for obj in doc.Objects:")
    lines.append("    if hasattr(obj, 'Shape') and obj.Shape.isValid():")
    lines.append("        export_objects.append(obj)")'''
    new_export = '''    hidden_names = sorted(
        _safe_name(part.get("name", ""))
        for part in project.get("parts", [])
        if not part.get("visible", True)
    )
    lines.append("# Collect visible shape objects for export")
    lines.append(f"hidden_objects = {hidden_names!r}")
    lines.append("export_objects = []")
    lines.append("for obj in doc.Objects:")
    lines.append("    if obj.Name not in hidden_objects and hasattr(obj, 'Shape') and obj.Shape.isValid():")
    lines.append("        export_objects.append(obj)")'''
    if old_booleans not in source or old_export not in source:
        raise RuntimeError("The installed FreeCAD harness no longer matches its compatibility patch.")
    patched = source.replace(old_booleans, new_booleans).replace(old_export, new_export)
    temporary = path.with_suffix(".agent8088.tmp")
    temporary.write_text(patched, encoding="utf-8")
    os.replace(temporary, path)


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
    if not isinstance(name, str):
        raise TypeError("CLI name must be a string.")
    value = name.strip().lower()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`":
        value = value[1:-1].strip()
    value = value.removeprefix("cli-anything-")
    if not value or len(value) > MAX_NAME_LENGTH or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
        raise ValueError(
            "CLI name must contain only letters, numbers, dots, underscores, or hyphens."
        )
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
        "installed": sorted(_load_ledger(root)),
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
    skill = skill_path.read_text(encoding="utf-8")
    if safe_name == "freecad":
        skill += (
            "\n## Agent8088 FreeCAD notes\n\n"
            "Boolean operations keep references to their source operands and "
            "automatically mark those operands `visible=false`. Never remove the "
            "operands: hidden parts stay in the editable project but are excluded "
            "from export. Verify final exported solids with `import info`; "
            "`document info` also counts hidden dependency parts. Paths passed to "
            "the harness are relative to `cwd`; when `cwd` is `artifacts/engine`, "
            "use `engine_project.json`, not `artifacts/engine/engine_project.json`.\n"
            "FreeCAD command arguments named `INDEX` are zero-based list indexes, "
            "not the one-based persistent `id`. Agent8088 adds an `index` field to "
            "part creation and boolean results; always reuse that `index` in later "
            "boolean, info, remove, and measure commands.\n"
        )
    return skill


def _freecad_remove_error(argv: list[str], workdir: Path) -> str | None:
    """Refuse removal of an operand that a boolean result still needs."""
    try:
        command = argv.index("part")
        if argv[command + 1] != "remove":
            return None
        index = int(argv[command + 2])
        project_flag = next(flag for flag in ("-p", "--project") if flag in argv)
        project_path = Path(argv[argv.index(project_flag) + 1])
        project_path = project_path if project_path.is_absolute() else workdir / project_path
        project = json.loads(project_path.read_text(encoding="utf-8"))
        parts = project["parts"]
        operand = parts[index]
        dependent = next(
            part for part in parts
            if part.get("type") in {"cut", "fuse", "common"}
            and operand.get("id") in {
                part.get("params", {}).get("base_id"),
                part.get("params", {}).get("tool_id"),
            }
        )
    except (ValueError, StopIteration, IndexError, KeyError, OSError,
            TypeError, json.JSONDecodeError):
        return None
    return (
        f"Error: Cannot remove part index {index} because boolean part "
        f"'{dependent.get('name')}' references it. Boolean operands are already "
        "hidden and excluded from export; keep them in the project."
    )


def _resolve_freecad_measurement(result: str, argv: list[str], workdir: Path,
                                 root: Path, timeout: int,
                                 path_prefix: Path | None) -> str:
    """Resolve measurements the harness defers for boolean parts."""
    try:
        payload = json.loads(result)
        command = argv.index("measure")
        kind = argv[command + 1]
        index = int(argv[command + 2])
        project_flag = next(flag for flag in ("-p", "--project") if flag in argv)
        project_path = Path(argv[argv.index(project_flag) + 1])
        project_path = project_path if project_path.is_absolute() else workdir / project_path
        if payload.get("deferred") is not True or kind not in {"bounding-box", "volume"}:
            return result
    except (ValueError, StopIteration, IndexError, TypeError, AttributeError,
            json.JSONDecodeError):
        return result
    resolver = r'''
import json
import sys
import tempfile
from pathlib import Path
from cli_anything.freecad.utils.freecad_backend import run_macro_content
from cli_anything.freecad.utils.freecad_macro_gen import generate_macro, _safe_name

project = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target_name = _safe_name(project["parts"][int(sys.argv[2])]["name"])
with tempfile.TemporaryDirectory(prefix="agent8088-freecad-measure-") as temporary:
    macro = generate_macro(project, str(Path(temporary) / "measure.step"), "step")
    macro += """
import json
target = doc.getObject(%r)
box = target.Shape.BoundBox
print("AGENT8088_METRICS=" + json.dumps({
    "volume": target.Shape.Volume,
    "min": {"x": box.XMin, "y": box.YMin, "z": box.ZMin},
    "max": {"x": box.XMax, "y": box.YMax, "z": box.ZMax},
    "size": {"x": box.XLength, "y": box.YLength, "z": box.ZLength},
}))
""" % target_name
    print(json.dumps(run_macro_content(macro)))
'''
    done = _run(
        [str(venv_python(root)), "-c", resolver, str(project_path), str(index)],
        root=root,
        timeout=timeout,
        cwd=workdir,
        managed_home=False,
        path_prefix=path_prefix,
    )
    try:
        backend = json.loads(done.stdout)
        marker = "AGENT8088_METRICS="
        metrics = json.loads(backend["stdout"].split(marker, 1)[1].splitlines()[0])
        if done.returncode or backend["returncode"]:
            return result
        if kind == "volume":
            payload["volume"] = metrics["volume"]
        else:
            payload.update({key: metrics[key] for key in ("min", "max", "size")})
        payload["deferred"] = False
        return json.dumps(payload, indent=2)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return result


def _freecad_expose_part_index(result: str, argv: list[str]) -> str:
    """Make the harness's zero-based part index explicit beside its 1-based ID."""
    try:
        command = argv.index("part")
        if argv[command + 1] not in {"add", "boolean"}:
            return result
        payload = json.loads(result)
        payload["index"] = int(payload["id"]) - 1
        payload["index_note"] = "Use index, not id, in commands that accept INDEX."
        return json.dumps(payload, indent=2)
    except (ValueError, IndexError, KeyError, TypeError, json.JSONDecodeError):
        return result


def run(config_path: Path | str, name: object, arguments: object, cwd: Path | str,
        *, timeout: int = 120) -> str:
    safe_name = _safe_name(name)
    argv = _safe_arguments(arguments)
    if safe_name == "freecad" and "--preset" not in argv:
        try:
            render_index = argv.index("render", argv.index("export") + 1)
        except ValueError:
            pass
        else:
            if (render_index + 1 < len(argv)
                    and Path(argv[render_index + 1]).suffix.lower() == ".stl"):
                argv.extend(("--preset", "stl"))
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
    if safe_name == "freecad" and (remove_error := _freecad_remove_error(argv, workdir)):
        return remove_error
    if safe_name == "freecad":
        _patch_freecad_harness(root)
    # The application receives the user's real HOME/USERPROFILE; CLI-Hub's
    # private home is only for registry cache and install bookkeeping.
    path_prefix = _freecad_macos_bin() if safe_name == "freecad" else None
    result = _result(_run([str(executable), *argv], root=root, timeout=timeout,
                          cwd=workdir, managed_home=False, path_prefix=path_prefix))
    if safe_name == "freecad":
        result = _freecad_expose_part_index(result, argv)
        result = _resolve_freecad_measurement(
            result, argv, workdir, root, timeout, path_prefix
        )
        try:
            command = argv.index("import")
            if argv[command + 1] != "info":
                return result
            step_path = Path(argv[command + 2])
            step_path = step_path if step_path.is_absolute() else workdir / step_path
            payload = json.loads(result)
            with step_path.open(encoding="utf-8", errors="ignore") as stream:
                count = sum(line.count("MANIFOLD_SOLID_BREP") for line in stream)
            if count:
                payload["estimated_objects"] = count
                return json.dumps(payload, indent=2)
        except (ValueError, IndexError, OSError, TypeError, json.JSONDecodeError):
            pass
    return result
