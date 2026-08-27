"""Worker for Agent8088's isolated build123d/text-to-cad CAD runtime."""
from __future__ import annotations

import ast
import json
import struct
import sys
import traceback
from pathlib import Path
from typing import Any

RESULT_START = "AGENT8088_CAD_RESULT_START"
RESULT_END = "AGENT8088_CAD_RESULT_END"

_ALLOWED_IMPORT_ROOTS = {"build123d", "math", "dataclasses", "typing"}
_BLOCKED_CALLS = {
    "breakpoint", "compile", "dir", "eval", "exec", "getattr", "globals",
    "hasattr", "help", "input", "locals", "open", "print", "setattr", "delattr",
    "vars", "__import__",
}
_BLOCKED_METHODS = {
    "communicate", "dump", "dumps", "load", "loads", "popen", "read", "run",
    "save", "saveas", "send", "system", "write",
}


def _validate_generator_source(path: Path) -> None:
    """Reject general-purpose Python capabilities before cadgen imports it."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"generator syntax error: {exc}") from exc
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    generators = [node for node in functions if node.name == "gen_step"]
    if len(generators) != 1 or generators[0].args.args:
        raise ValueError("generator must define gen_step() with no arguments")
    param_assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "PARAMS"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    if len(param_assignments) != 1:
        raise ValueError("generator parameters may not replace the injected PARAMS object")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in _ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"generator import is not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if not root or root not in _ALLOWED_IMPORT_ROOTS or node.level:
                raise ValueError(f"generator import is not allowed: {node.module or '<relative>'}")
            for alias in node.names:
                if alias.name.startswith("import_") or alias.name.startswith("export_"):
                    raise ValueError(f"generator I/O import is not allowed: {alias.name}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"private/dunder attribute access is not allowed: {node.attr}")
        elif isinstance(node, ast.Attribute) and node.attr.lower() in _BLOCKED_METHODS:
            raise ValueError(f"file-capable method is not allowed in a generator: {node.attr}()")
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(f"dunder name access is not allowed: {node.id}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (
            node.func.id.startswith("import_") or node.func.id.startswith("export_")
        ):
            raise ValueError(f"generator I/O call is not allowed: {node.func.id}()")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and (
            node.func.attr.startswith("import_") or node.func.attr.startswith("export_")
        ):
            raise ValueError(f"generator I/O call is not allowed: {node.func.attr}()")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            raise ValueError(f"generator call is not allowed: {node.func.id}()")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.While, ast.Yield, ast.YieldFrom)):
            raise ValueError("async, while-loop, and generator execution are not allowed in CAD source")


def _load_shape(path: Path):
    from build123d import Compound, Mesher, import_brep, import_step, import_stl

    suffix = path.suffix.lower()
    if suffix in {".step", ".stp"}:
        return import_step(path)
    if suffix == ".brep":
        return import_brep(path)
    if suffix == ".stl":
        return import_stl(path)
    if suffix == ".3mf":
        shapes = Mesher().read(path)
        if not shapes:
            raise ValueError("3MF contains no geometry")
        return shapes[0] if len(shapes) == 1 else Compound(children=shapes)
    raise ValueError(f"cannot load {suffix or 'extensionless'} geometry")


def _bbox(shape) -> dict[str, list[float]]:
    box = shape.bounding_box()
    minimum = [float(box.min.X), float(box.min.Y), float(box.min.Z)]
    maximum = [float(box.max.X), float(box.max.Y), float(box.max.Z)]
    return {
        "min": minimum,
        "max": maximum,
        "size": [maximum[index] - minimum[index] for index in range(3)],
    }


def _metrics(shape, *, mesh: bool = False) -> dict[str, Any]:
    from cadgen.validity import check_occurrence_shape

    solids = list(shape.solids())
    volume = sum(float(solid.volume) for solid in solids)
    # import_stl returns a triangulated Face, not a BREP solid. Passing that
    # surface through OCP's solid self-intersection checker crashes the native
    # process on Windows instead of raising. Mesh exports are validated by
    # existence and successful parse; BREP topology is validated here.
    raw_validity = (
        {"reasons": [], "volumes": []}
        if mesh
        else check_occurrence_shape(shape.wrapped)
    )
    reasons = list(raw_validity.get("reasons") or [])
    if not mesh and not solids:
        reasons.append("no solid bodies")
    if not mesh and volume <= 0:
        reasons.append("non-positive solid volume")
    result = {
        "solid_count": len(solids),
        "volume": volume,
        "bounding_box": _bbox(shape),
        "validity": {
            "ok": not reasons,
            "reasons": reasons,
            "volumes": raw_validity.get("volumes") or [],
        },
    }
    if mesh:
        result["mesh_count"] = 1
    return result


def _glb_metrics(path: Path) -> dict[str, Any]:
    """Validate the GLB container and require referenced, non-empty geometry."""
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("GLB is too small to contain geometry")
    magic, version, declared_length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError("GLB header is invalid")
    offset = 12
    document = None
    binary_bytes = 0
    while offset + 8 <= len(data):
        length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        end = offset + length
        if end > len(data):
            raise ValueError("GLB chunk extends beyond the file")
        if chunk_type == b"JSON":
            document = json.loads(data[offset:end].rstrip(b" \t\r\n\0").decode("utf-8"))
        elif chunk_type == b"BIN\0":
            binary_bytes += length
        offset = end
    if not isinstance(document, dict):
        raise TypeError("GLB has no JSON scene")
    meshes = document.get("meshes") or []
    accessors = document.get("accessors") or []
    buffers = document.get("buffers") or []
    primitives = [
        primitive
        for mesh in meshes if isinstance(mesh, dict)
        for primitive in (mesh.get("primitives") or []) if isinstance(primitive, dict)
    ]
    positions = [
        primitive.get("attributes", {}).get("POSITION")
        for primitive in primitives
        if isinstance(primitive.get("attributes"), dict)
    ]
    if (
        not meshes or not primitives or not accessors or not buffers
        or not positions or not binary_bytes
        or not any(int(item.get("byteLength") or 0) > 0 for item in buffers if isinstance(item, dict))
    ):
        raise ValueError("GLB contains no referenced mesh geometry")
    if any(not isinstance(index, int) or not 0 <= index < len(accessors) for index in positions):
        raise ValueError("GLB POSITION accessor is invalid")
    return {
        "solid_count": 0,
        "mesh_count": len(meshes),
        "volume": None,
        "validity": {"ok": True, "reasons": [], "volumes": []},
    }


def _write_shape(shape, output: Path) -> None:
    from build123d import Mesher, export_brep, export_gltf, export_step, export_stl

    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix.lower()
    if suffix in {".step", ".stp"}:
        if not export_step(shape, output):
            raise RuntimeError("build123d STEP export returned false")
    elif suffix == ".stl":
        if not export_stl(shape, output):
            raise RuntimeError("build123d STL export returned false")
    elif suffix == ".brep":
        if not export_brep(shape, output):
            raise RuntimeError("build123d BREP export returned false")
    elif suffix == ".glb":
        if not export_gltf(shape, output, binary=True):
            raise RuntimeError("build123d glTF export returned false")
    elif suffix == ".3mf":
        mesher = Mesher()
        mesher.add_shape(shape)
        mesher.write(output)
    else:
        raise ValueError(f"unsupported output format: {suffix}")
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError(f"CAD exporter did not create a usable file: {output}")


def _verify_export(path: Path) -> None:
    """Parse or render every export before it can be reported as successful."""
    suffix = path.suffix.lower()
    if suffix in {".step", ".stp", ".brep"}:
        result = _metrics(_load_shape(path))
        if not result["validity"]["ok"]:
            raise RuntimeError(
                f"{suffix} export failed solid validation: "
                + ", ".join(result["validity"]["reasons"])
            )
        return
    if suffix == ".stl":
        # Successful OpenCascade parsing is the available integrity check for
        # this surface mesh. It is intentionally not called a watertight solid.
        _load_shape(path)
        return
    if suffix == ".3mf":
        from build123d import Mesher

        mesh = Mesher()
        mesh.read(path)
        if not mesh.mesh_count:
            raise RuntimeError("3MF export contains no meshes")
        return
    if suffix == ".glb":
        _glb_metrics(path)
        # Structural verification above rejects a blank scene. cadgen's own
        # browser pipeline then proves the scene can actually be consumed.
        verification = path.with_name(f".{path.stem}-verify.png")
        try:
            _render(path, verification)
            if not verification.is_file() or verification.stat().st_size <= 0:
                raise RuntimeError("GLB verification produced no render")
        finally:
            verification.unlink(missing_ok=True)
        return
    raise ValueError(f"unsupported output format: {suffix}")


def _export_step_meshes(step: Path, outputs: list[tuple[str, Path]], workspace: Path) -> None:
    """Use text-to-cad's purpose-built STEP mesh exporters in one scene pass."""
    from cadgen.step_export_target import export_cad_target

    payload = export_cad_target(
        step,
        [(name, path) for name, path in outputs],
        repo_root=workspace,
        verbose=False,
    )
    if not payload.get("ok"):
        raise RuntimeError("text-to-cad mesh export did not report success")
    for _, output in outputs:
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(f"text-to-cad did not create {output}")
        _verify_export(output)


def _make_primitive(name: str, dimensions: dict[str, float]):
    from build123d import Box, Cone, Cylinder, Sphere

    if name == "box":
        return Box(dimensions["length"], dimensions["width"], dimensions["height"])
    if name == "cylinder":
        return Cylinder(dimensions["radius"], dimensions["height"])
    if name == "sphere":
        return Sphere(dimensions["radius"])
    if name == "cone":
        return Cone(dimensions["bottom_radius"], dimensions["top_radius"], dimensions["height"])
    if name == "tube":
        return (
            Cylinder(dimensions["outer_radius"], dimensions["height"])
            - Cylinder(dimensions["inner_radius"], dimensions["height"])
        )
    raise ValueError(f"unknown primitive: {name}")


def _render(model: Path, output: Path) -> None:
    import io

    from cadgen.snapshot_cli import run_snapshot_cli

    runtime_dir = Path(__file__).with_name("cad_snapshot_runtime")
    if not (runtime_dir / "render.html").is_file() or not (runtime_dir / "snapshot-render.js").is_file():
        raise RuntimeError("packaged text-to-cad snapshot runtime is missing")
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    code = run_snapshot_cli(
        ["--input", str(model), "--output", str(output), "--camera", "iso",
         "--size-profile", "presentation", "--json"],
        kinds=("step", "stp", "3mf", "glb", "stl"), runtime_dir=runtime_dir,
        cwd=model.parent, stdout=captured_out, stderr=captured_err,
    )
    try:
        payload = json.loads(captured_out.getvalue().strip())
        rendered = Path(payload["outputs"][0]["path"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        detail = captured_err.getvalue().strip() or captured_out.getvalue().strip()
        raise RuntimeError(f"text-to-cad snapshot returned no artifact: {detail[:500]}") from exc
    if code or not rendered.is_file() or rendered.stat().st_size <= 0:
        detail = captured_err.getvalue().strip()
        raise RuntimeError(f"text-to-cad snapshot failed (exit {code}): {detail[:500]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered.replace(output)


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _require_workspace_paths(request: dict[str, Any]) -> None:
    workspace = Path(request["workspace"]).resolve()
    for key in ("input", "output", "source", "params", "report", "preview"):
        raw = str(request.get(key) or "").strip()
        if not raw:
            continue
        candidate = Path(raw).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"CAD {key} path is outside its authorized workspace") from exc


def _action(request: dict[str, Any]) -> dict[str, Any]:
    _require_workspace_paths(request)
    action = str(request.get("action") or "")
    if action == "primitive":
        output = Path(request["output"]).resolve()
        shape = _make_primitive(str(request["shape"]), dict(request["dimensions"]))
        shape.label = str(request["shape"])
        _write_shape(shape, output)
        _verify_export(output)
        reopened = _load_shape(output) if output.suffix.lower() in {".step", ".stp", ".brep", ".stl"} else shape
        result = _metrics(reopened, mesh=output.suffix.lower() == ".stl")
        if not result["validity"]["ok"]:
            raise RuntimeError("generated primitive failed validation: " + ", ".join(result["validity"]["reasons"]))
        return {"ok": True, **result}

    if action == "inspect":
        input_path = Path(request["input"]).resolve()
        if input_path.suffix.lower() == ".glb":
            return {"ok": True, **_glb_metrics(input_path)}
        shape = _load_shape(input_path)
        return {
            "ok": True,
            **_metrics(shape, mesh=input_path.suffix.lower() in {".stl", ".3mf"}),
        }

    if action == "convert":
        input_path = Path(request["input"]).resolve()
        shape = _load_shape(input_path)
        output = Path(request["output"]).resolve()
        if input_path.suffix.lower() in {".step", ".stp"} and output.suffix.lower() in {".stl", ".3mf", ".glb"}:
            _export_step_meshes(
                input_path, [(output.suffix.lower().lstrip("."), output)],
                Path(request["workspace"]).resolve(),
            )
        else:
            _write_shape(shape, output)
            _verify_export(output)
        reopened = _load_shape(output) if output.suffix.lower() in {".step", ".stp", ".brep", ".stl"} else shape
        return {
            "ok": True,
            **_metrics(reopened, mesh=output.suffix.lower() == ".stl"),
        }

    if action in {"generate", "validate"}:
        if action == "generate":
            from cadgen.generation import generate_step_targets

            source = Path(request["source"]).resolve()
            output = Path(request["output"]).resolve()
            _validate_generator_source(source)
            exit_code = generate_step_targets(
                [f"{source}={output}"], force=True, verbose=False,
                json_output=False, lock_timeout_s=30.0,
            )
            if exit_code:
                raise RuntimeError(f"text-to-cad generation failed (exit {exit_code})")
        else:
            output = Path(request["input"]).resolve()

        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(f"STEP artifact was not created: {output}")
        shape = _load_shape(output)
        result = _metrics(shape)
        if not result["validity"]["ok"]:
            raise RuntimeError("generated STEP failed validation: " + ", ".join(result["validity"]["reasons"]))

        if action == "generate":
            requested = [str(name) for name in (request.get("formats") or []) if name != "step"]
            mesh_outputs = [
                (name, output.with_suffix("." + name))
                for name in requested if name in {"stl", "3mf", "glb"}
            ]
            if mesh_outputs:
                _export_step_meshes(output, mesh_outputs, Path(request["workspace"]).resolve())
            for name in requested:
                if name not in {"stl", "3mf", "glb"}:
                    exported = output.with_suffix("." + str(name))
                    _write_shape(shape, exported)
                    _verify_export(exported)
        preview_text = str(request.get("preview") or "").strip()
        if preview_text:
            _render(output, Path(preview_text).resolve())
        report = Path(request["report"]).resolve()
        report_payload = {
            "engine": {"geometry": "build123d", "workflow": "text-to-cad/cadgen"},
            "source": str(request.get("source") or ""),
            "step": str(output),
            "preview": preview_text,
            **result,
        }
        _write_report(report, report_payload)
        return {"ok": True, "report": str(report), "preview": preview_text, **result}

    raise ValueError(f"unknown CAD worker action: {action}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    result: dict[str, Any]
    try:
        if len(args) != 1:
            raise ValueError("CAD worker requires one JSON request path")
        request = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise TypeError("CAD request must be a JSON object")
        result = _action(request)
    except Exception as exc:  # noqa: BLE001 - worker boundary returns structured failures
        result = {"ok": False, "error": str(exc), "traceback": traceback.format_exc(limit=12)}
    print(RESULT_START)
    print(json.dumps(result, ensure_ascii=False))
    print(RESULT_END)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
