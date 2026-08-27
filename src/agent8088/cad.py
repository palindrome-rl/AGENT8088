"""Deterministic CAD generation through build123d and text-to-cad's cadgen.

FreeCAD used to be Agent8088's CAD engine. That made every operation depend on
a large desktop application and encouraged complex requests to fall back to raw
FreeCAD Python through ``execute_shell``. This module keeps the established
public tool functions but sends their work to an isolated CAD environment built
from two reviewed upstream projects:

* build123d is the parametric OpenCascade geometry engine;
* cadgen, published by text-to-cad, supplies STEP-first generation, topology
  validation, assembly metadata and browser snapshot machinery.

The main Agent8088 environment never imports either dependency. A small worker
runs under the dedicated interpreter and communicates with this module using a
JSON request plus a single framed JSON result. Subprocess exit status is never
enough for success: every requested artifact is checked on disk and the worker
reopens generated geometry before reporting it.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .documents import _readable_or_reason

MAX_CAD_BYTES = 200 * 1024 * 1024
MAX_GENERATOR_BYTES = 256 * 1024

CADGEN_VERSION = "0.4.26"
BUILD123D_VERSION = "0.11.1"

CAD_EXTENSIONS = (
    ".step", ".stp", ".stl", ".3mf", ".glb", ".brep",
)
CONVERTIBLE_CAD_TARGETS = ("step", "stl", "3mf", "glb", "brep")
CAD_PRIMITIVES = ("box", "cylinder", "sphere", "cone", "tube")

_RESULT_START = "AGENT8088_CAD_RESULT_START"
_RESULT_END = "AGENT8088_CAD_RESULT_END"


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


def cad_runtime_status(timeout: int = 15) -> dict[str, Any]:
    """Return a real import probe for the isolated CAD runtime."""
    python = cad_runtime_python()
    result: dict[str, Any] = {
        "available": False,
        "python": str(python),
        "root": str(cad_runtime_root()),
        "cadgen": CADGEN_VERSION,
        "build123d": BUILD123D_VERSION,
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
        result["available"] = True
        result["installed_versions"] = versions
    else:
        detail = (done.stderr or done.stdout or f"exit {done.returncode}").strip()
        result["reason"] = detail[:500]
    return result


_NOT_INSTALLED_MESSAGE = (
    "Agent8088's advanced CAD runtime is not installed, so this CAD operation "
    "cannot run. Re-run the Agent8088 installer to install the pinned build123d "
    f"{BUILD123D_VERSION} + text-to-cad cadgen {CADGEN_VERSION} runtime."
)


def _worker_path() -> Path:
    return Path(__file__).with_name("cad_worker.py")


def _worker_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_agent_home() / "playwright-browsers"))
    return env


def _run_worker(request: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Run one structured worker request and require a framed JSON response."""
    python = cad_runtime_python()
    if not python.is_file():
        return {"ok": False, "error": _NOT_INSTALLED_MESSAGE}
    worker = _worker_path()
    if not worker.is_file():
        return {"ok": False, "error": f"CAD worker is missing: {worker}"}

    request_dir = Path(str(request.get("workspace") or cad_runtime_root()))
    request_dir.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=".agent8088-cad-", suffix=".json", dir=request_dir)
    request_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(request, handle, ensure_ascii=False)
        try:
            done = subprocess.run(
                [str(python), "-I", str(worker), str(request_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=max(1, int(timeout)), shell=False, env=_worker_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"CAD operation timed out after {timeout}s."}
        except OSError as exc:
            return {"ok": False, "error": f"Could not start the CAD runtime: {exc}"}

        stdout = done.stdout or ""
        if _RESULT_START not in stdout or _RESULT_END not in stdout:
            detail = (done.stderr or stdout or f"worker exited {done.returncode}").strip()
            return {"ok": False, "error": f"CAD worker failed: {detail[:1000]}"}
        payload = stdout.split(_RESULT_START, 1)[1].split(_RESULT_END, 1)[0].strip()
        try:
            result = json.loads(payload)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"CAD worker returned invalid JSON: {exc}"}
        if not isinstance(result, dict):
            return {"ok": False, "error": "CAD worker returned a non-object result."}
        if done.returncode and result.get("ok"):
            return {"ok": False, "error": f"CAD worker exited {done.returncode} after claiming success."}
        return result
    finally:
        request_path.unlink(missing_ok=True)


def _existing_artifact(path: Path, label: str) -> str | None:
    if not path.is_file():
        return f"{label} was not created: {path}"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"Could not inspect {label}: {exc}"
    if size <= 0:
        return f"{label} is empty: {path}"
    return None


def _format_metrics(data: dict[str, Any]) -> str:
    bbox = data.get("bounding_box") or {}
    size = bbox.get("size") or []
    lines = []
    if len(size) == 3:
        lines.append(
            "Bounding box: " + " x ".join(f"{float(value):.3f}" for value in size)
            + " mm (X x Y x Z)"
        )
    if data.get("mesh_count") is not None:
        lines.append(f"Mesh bodies: {int(data.get('mesh_count') or 0)}")
    else:
        lines.append(f"Solids: {int(data.get('solid_count') or 0)}")
    if data.get("volume") is not None:
        lines.append(f"Volume: {float(data['volume']):.3f} mm^3")
    validity = data.get("validity") or {}
    lines.append("Geometry: valid" if validity.get("ok") else "Geometry: INVALID")
    reasons = validity.get("reasons") or []
    if reasons:
        lines.append("Validation findings: " + ", ".join(str(item) for item in reasons))
    return "\n".join(lines)


def extract_info(path, max_bytes: int = MAX_CAD_BYTES):
    """Return a deterministic geometry summary, or None for a non-CAD file."""
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
    result = _run_worker(
        {"action": "inspect", "input": str(path.resolve()), "workspace": str(path.parent.resolve())},
        timeout=180,
    )
    if not result.get("ok"):
        return f"Could not inspect {path.name}: {result.get('error', 'unknown CAD error')}"
    return f"CAD file: {path.name}\n" + _format_metrics(result)


def convert_cad(path, target_format: str, timeout: int = 300) -> str:
    """Convert a supported CAD artifact using build123d/OpenCascade."""
    path = Path(path)
    target = (target_format or "").strip().lower().lstrip(".")
    if target not in CONVERTIBLE_CAD_TARGETS:
        return (
            f"Cannot convert to '{target}'. Supported targets: "
            f"{', '.join(CONVERTIBLE_CAD_TARGETS)}."
        )
    if not path.exists():
        return f"Cannot convert: {path} does not exist."
    if path.suffix.lower().lstrip(".") == target:
        return f"Cannot convert {path.name}: source and target formats are both {target}."
    if path.suffix.lower() == ".stl" and target in {"step", "brep"}:
        return (
            "Cannot convert a triangulated STL mesh into a trustworthy solid B-rep. "
            "Use the original STEP/BREP model, or regenerate the part parametrically."
        )
    unreadable = _readable_or_reason(path)
    if unreadable:
        return unreadable
    output = path.with_suffix("." + target)
    result = _run_worker({
        "action": "convert", "input": str(path.resolve()), "output": str(output.resolve()),
        "workspace": str(path.parent.resolve()),
    }, timeout=timeout)
    if not result.get("ok"):
        return f"Conversion failed: {result.get('error', 'unknown CAD error')}"
    problem = _existing_artifact(output, "converted CAD artifact")
    if problem:
        return f"Conversion failed: {problem}"
    return f"Converted {path.name} to {output.name} ({output.stat().st_size} bytes).\n" + _format_metrics(result)


def _parse_dimensions(shape: str, dimensions: str) -> dict[str, float]:
    keys = {
        "box": ("length", "width", "height"),
        "cylinder": ("radius", "height"),
        "sphere": ("radius",),
        "cone": ("bottom_radius", "top_radius", "height"),
        "tube": ("outer_radius", "inner_radius", "height"),
    }[shape]
    raw = (dimensions or "").strip().lower().replace(" ", "")
    if "=" in raw:
        aliases = {
            "r": "radius", "l": "length", "w": "width", "h": "height",
            "r1": "bottom_radius", "r2": "top_radius", "outer": "outer_radius",
            "inner": "inner_radius", "od": "outer_diameter", "id": "inner_diameter",
        }
        parsed: dict[str, float] = {}
        for pair in raw.split(","):
            if "=" not in pair:
                raise ValueError(f"Expected key=value but got '{pair}'.")
            key, value = pair.split("=", 1)
            key = aliases.get(key, key)
            try:
                parsed[key] = float(value)
            except ValueError as exc:
                raise ValueError(f"'{value}' is not a number.") from exc
        if "outer_diameter" in parsed:
            parsed["outer_radius"] = parsed["outer_diameter"] / 2
        if "inner_diameter" in parsed:
            parsed["inner_radius"] = parsed["inner_diameter"] / 2
        missing = [key for key in keys if key not in parsed]
        if missing:
            raise ValueError("Missing dimension(s): " + ", ".join(missing) + ".")
        result = {key: parsed[key] for key in keys}
    else:
        values = raw.replace("r", "")
        parts = values.split("x") if values else []
        if len(parts) != len(keys):
            raise ValueError(f"{shape} needs {len(keys)} dimension value(s); got {len(parts)}.")
        try:
            result = dict(zip(keys, (float(value) for value in parts)))
        except ValueError as exc:
            bad = next((value for value in parts if not _is_number(value)), "")
            raise ValueError(f"'{bad}' is not a number.") from exc
    if any(value <= 0 for value in result.values()):
        raise ValueError("All dimensions must be greater than zero.")
    if shape == "tube" and result["inner_radius"] >= result["outer_radius"]:
        raise ValueError("Tube inner radius must be smaller than outer radius.")
    return result


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def create_cad_part(path, shape: str, dimensions: str, timeout: int = 300) -> str:
    """Create and validate one primitive with build123d."""
    path = Path(path)
    shape = (shape or "").strip().lower()
    if shape not in CAD_PRIMITIVES:
        return f"Unknown shape '{shape}'. Supported shapes: {', '.join(CAD_PRIMITIVES)}."
    extension = path.suffix.lower().lstrip(".")
    if extension not in CONVERTIBLE_CAD_TARGETS:
        return (
            f"Supported output formats: {', '.join(CONVERTIBLE_CAD_TARGETS)}. "
            "Native .FCStd output is no longer generated; use STEP for editable interchange."
        )
    try:
        dims = _parse_dimensions(shape, dimensions)
    except ValueError as exc:
        return f"Invalid dimensions for {shape}: {exc}"
    result = _run_worker({
        "action": "primitive", "shape": shape, "dimensions": dims,
        "output": str(path.resolve()), "workspace": str(path.parent.resolve()),
    }, timeout=timeout)
    if not result.get("ok"):
        return f"Generation failed: {result.get('error', 'unknown CAD error')}"
    problem = _existing_artifact(path, "CAD artifact")
    if problem:
        return f"Generation failed: {problem}"
    return (
        f"Created {path.name} ({path.stat().st_size} bytes) with build123d.\n"
        + _format_metrics(result)
    )


def generate_cad_model(path, source: str, parameters: str = "{}",
                       formats: str = "step,stl", timeout: int = 600) -> str:
    """Generate a verified STEP-first model from constrained gen_step() source."""
    path = Path(path)
    if path.suffix.lower() not in (".step", ".stp"):
        return "Advanced CAD generation requires a .step output filename."
    if not isinstance(source, str) or not source.strip():
        return "CAD source must define a non-empty gen_step() function."
    if len(source.encode("utf-8")) > MAX_GENERATOR_BYTES:
        return f"CAD source is too large (limit: {MAX_GENERATOR_BYTES} bytes)."
    try:
        params = json.loads(parameters or "{}")
    except json.JSONDecodeError as exc:
        return f"CAD parameters must be a JSON object: {exc}"
    if not isinstance(params, dict):
        return "CAD parameters must be a JSON object."

    requested = []
    for item in (formats or "step").split(","):
        value = item.strip().lower().lstrip(".")
        if value and value not in requested:
            requested.append(value)
    unsupported = [item for item in requested if item not in CONVERTIBLE_CAD_TARGETS]
    if unsupported:
        return "Unsupported CAD output format(s): " + ", ".join(unsupported) + "."
    if "step" not in requested:
        requested.insert(0, "step")

    path.parent.mkdir(parents=True, exist_ok=True)
    source_path = path.with_suffix(".step.py")
    params_path = path.with_suffix(".params.json")
    report_path = path.with_suffix(".report.json")
    preview_path = path.with_suffix(".preview.png")
    header = (
        "# Parametric build123d model generated by Agent8088.\n"
        "# Geometry workflow and validation use text-to-cad cadgen.\n"
        f"PARAMS = {params!r}\n\n"
    )
    source_path.write_text(header + source.rstrip() + "\n", encoding="utf-8", newline="\n")
    params_path.write_text(
        json.dumps(params, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )

    result = _run_worker({
        "action": "generate", "source": str(source_path.resolve()),
        "output": str(path.resolve()), "params": str(params_path.resolve()),
        "report": str(report_path.resolve()), "preview": str(preview_path.resolve()),
        "formats": requested, "workspace": str(path.parent.resolve()),
    }, timeout=timeout)
    if not result.get("ok"):
        return f"CAD generation failed: {result.get('error', 'unknown CAD error')}"

    expected = [path, source_path, params_path, report_path, preview_path]
    for item in requested:
        if item != "step":
            expected.append(path.with_suffix("." + item))
    failures = [problem for item in expected if (problem := _existing_artifact(item, item.name))]
    if failures:
        return "CAD generation failed verification: " + "; ".join(failures)

    artifacts = "\n".join(f"  - {item}" for item in expected)
    return (
        f"Generated and verified {path.name} with build123d + text-to-cad.\n"
        + _format_metrics(result)
        + "\nArtifacts:\n" + artifacts
    )


def validate_cad_model(path, render: bool = True, timeout: int = 300) -> str:
    """Reopen, validate and optionally render an existing STEP model."""
    path = Path(path)
    if not path.exists():
        return f"Cannot validate: {path} does not exist."
    report = path.with_suffix(".report.json")
    preview = path.with_suffix(".preview.png") if render else None
    result = _run_worker({
        "action": "validate", "input": str(path.resolve()),
        "report": str(report.resolve()),
        "preview": str(preview.resolve()) if preview else "",
        "workspace": str(path.parent.resolve()),
    }, timeout=timeout)
    if not result.get("ok"):
        return f"CAD validation failed: {result.get('error', 'unknown CAD error')}"
    expected = [report] + ([preview] if preview else [])
    failures = [problem for item in expected if (problem := _existing_artifact(item, item.name))]
    if failures:
        return "CAD validation failed verification: " + "; ".join(failures)
    return f"Validated {path.name}.\n" + _format_metrics(result)
