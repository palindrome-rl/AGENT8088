"""Worker for Agent8088's isolated build123d/text-to-cad CAD runtime."""
from __future__ import annotations

import ast
import json
import math
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

_DESIGN_PRIMITIVES = {"box", "cylinder", "sphere", "cone", "tube"}
_MAX_DESIGN_COMPONENTS = 256
_MAX_DESIGN_PRIMITIVES = 2048
_MAX_PLACEMENTS_PER_PRIMITIVE = 512
_MAX_ABS_DIMENSION_MM = 1_000_000.0


def _expression_value(value: Any, parameters: dict[str, Any]) -> float:
    """Resolve one finite number or a small arithmetic expression.

    Declarative CAD deliberately has no Python escape hatch. Expressions may
    reference numeric parameters and use arithmetic only; calls, attributes,
    indexing and every other Python construct are refused.
    """
    if isinstance(value, bool):
        raise TypeError("boolean is not a CAD dimension")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            tree = ast.parse(value.strip(), mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"invalid CAD expression {value!r}") from exc

        def evaluate(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                    and not isinstance(node.value, bool):
                return float(node.value)
            if isinstance(node, ast.Name):
                if node.id not in parameters:
                    raise ValueError(f"unknown CAD parameter: {node.id}")
                return _expression_value(parameters[node.id], parameters)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                operand = evaluate(node.operand)
                return operand if isinstance(node.op, ast.UAdd) else -operand
            if isinstance(node, ast.BinOp) and isinstance(
                    node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
                if abs(right) > 8:
                    raise ValueError("CAD expression exponent is outside -8..8")
                return left ** right
            raise ValueError("CAD expressions allow only numbers, parameters, and arithmetic")

        result = evaluate(tree)
    else:
        raise TypeError(
            f"CAD dimension must be a number or expression, got {type(value).__name__}"
        )
    if not math.isfinite(result):
        raise ValueError("CAD dimension must be finite")
    if abs(result) > _MAX_ABS_DIMENSION_MM:
        raise ValueError(
            f"CAD dimension exceeds the {_MAX_ABS_DIMENSION_MM:g} mm safety bound"
        )
    return float(result)


def _vector(value: Any, parameters: dict[str, Any], field: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{field} must contain exactly three values")
    return tuple(_expression_value(item, parameters) for item in value)


def _primitive_shape(spec: dict[str, Any], parameters: dict[str, Any]):
    """Compile one reviewed JSON primitive into build123d geometry."""
    from build123d import Align, Box, Cone, Cylinder, Pos, Rot, Sphere

    kind = str(spec.get("type") or "").strip().lower()
    if kind not in _DESIGN_PRIMITIVES:
        raise ValueError(
            f"unsupported declarative primitive {kind!r}; use one of "
            + ", ".join(sorted(_DESIGN_PRIMITIVES))
        )
    corner_align = (Align.MIN, Align.MIN, Align.MIN)
    axis_align = (Align.CENTER, Align.CENTER, Align.MIN)
    if kind == "box":
        size = _vector(spec.get("size"), parameters, "box.size")
        if any(item <= 0 for item in size):
            raise ValueError("box dimensions must be greater than zero")
        shape = Box(*size, align=corner_align)
    elif kind == "cylinder":
        radius = _expression_value(spec.get("radius"), parameters)
        height = _expression_value(spec.get("height"), parameters)
        if radius <= 0 or height <= 0:
            raise ValueError("cylinder radius and height must be greater than zero")
        shape = Cylinder(radius, height, align=axis_align)
    elif kind == "sphere":
        radius = _expression_value(spec.get("radius"), parameters)
        if radius <= 0:
            raise ValueError("sphere radius must be greater than zero")
        shape = Sphere(radius)
    elif kind == "cone":
        radius1 = _expression_value(spec.get("radius1"), parameters)
        radius2 = _expression_value(spec.get("radius2"), parameters)
        height = _expression_value(spec.get("height"), parameters)
        if radius1 < 0 or radius2 < 0 or not (radius1 or radius2) or height <= 0:
            raise ValueError("cone radii must be non-negative and height positive")
        shape = Cone(radius1, radius2, height, align=axis_align)
    else:
        outer = _expression_value(spec.get("outer_radius"), parameters)
        inner = _expression_value(spec.get("inner_radius"), parameters)
        height = _expression_value(spec.get("height"), parameters)
        if inner <= 0 or outer <= inner or height <= 0:
            raise ValueError("tube requires outer_radius > inner_radius > 0 and height > 0")
        shape = (
            Cylinder(outer, height, align=axis_align)
            - Cylinder(inner, height, align=axis_align)
        )

    rotate = _vector(spec.get("rotate", [0, 0, 0]), parameters, "rotate")
    at = _vector(spec.get("at", [0, 0, 0]), parameters, "at")
    if any(rotate):
        shape = Rot(*rotate) * shape
    if any(at):
        shape = Pos(*at) * shape
    return shape


def _expanded_primitives(spec: dict[str, Any], parameters: dict[str, Any]):
    """Yield one primitive at each declared placement without source expansion."""
    placements = spec.get("placements")
    if placements is None:
        yield _primitive_shape(spec, parameters)
        return
    if not isinstance(placements, list) or not placements:
        raise ValueError("placements must be a non-empty array of [x, y, z] vectors")
    if len(placements) > _MAX_PLACEMENTS_PER_PRIMITIVE:
        raise ValueError(
            f"one primitive may have at most {_MAX_PLACEMENTS_PER_PRIMITIVE} placements"
        )
    base_at = _vector(spec.get("at", [0, 0, 0]), parameters, "at")
    for placement in placements:
        offset = _vector(placement, parameters, "placement")
        instance = dict(spec)
        instance.pop("placements", None)
        instance["at"] = [base_at[index] + offset[index] for index in range(3)]
        yield _primitive_shape(instance, parameters)


def _build_design(design: dict[str, Any]):
    """Compile the bounded declarative schema to a labeled build123d assembly."""
    if int(design.get("schema_version", 1)) != 1:
        raise ValueError("unsupported CAD design schema_version; expected 1")
    if str(design.get("units", "mm")).lower() != "mm":
        raise ValueError("declarative CAD currently requires millimetres")
    if str(design.get("interference_policy", "error")).lower() != "error":
        raise ValueError(
            "declarative CAD always rejects volumetric assembly overlap; "
            "interference_policy cannot disable this validation"
        )
    from build123d import Compound

    parameters = design.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise TypeError("design.parameters must be a JSON object")
    # Resolve every scalar once up front. This catches cycles/unknown names
    # before OpenCascade starts doing expensive work.
    for name, value in parameters.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"invalid CAD parameter name: {name!r}")
        _expression_value(value, parameters)

    components = design.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("design.components must be a non-empty array")
    if len(components) > _MAX_DESIGN_COMPONENTS:
        raise ValueError(f"design exceeds {_MAX_DESIGN_COMPONENTS} components")
    names: set[str] = set()
    built = []
    solid_names: list[str] = []
    primitive_count = 0
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise TypeError(f"component {index} must be a JSON object")
        name = str(component.get("name") or "").strip()
        if not name or name in names:
            raise ValueError(f"component names must be non-empty and unique: {name!r}")
        names.add(name)
        additions = component.get("add")
        cuts = component.get("cut") or []
        if not isinstance(additions, list) or not additions:
            raise ValueError(f"component {name!r} needs a non-empty add array")
        if not isinstance(cuts, list):
            raise TypeError(f"component {name!r} cut must be an array")
        add_shapes = []
        for primitive in additions:
            if not isinstance(primitive, dict):
                raise TypeError(f"component {name!r} has a non-object primitive")
            instances = list(_expanded_primitives(primitive, parameters))
            primitive_count += len(instances)
            add_shapes.extend(instances)
        if primitive_count > _MAX_DESIGN_PRIMITIVES:
            raise ValueError(f"design exceeds {_MAX_DESIGN_PRIMITIVES} primitive instances")
        shape = add_shapes[0]
        if len(add_shapes) > 1:
            shape = shape.fuse(*add_shapes[1:])
        for primitive in cuts:
            if not isinstance(primitive, dict):
                raise TypeError(f"component {name!r} has a non-object cut")
            for cutter in _expanded_primitives(primitive, parameters):
                primitive_count += 1
                if primitive_count > _MAX_DESIGN_PRIMITIVES:
                    raise ValueError(f"design exceeds {_MAX_DESIGN_PRIMITIVES} primitive instances")
                shape = shape - cutter
        shape.label = name
        built.append(shape)
        component_solids = list(shape.solids())
        solid_names.extend(
            name if len(component_solids) == 1 else f"{name}[{index}]"
            for index in range(len(component_solids))
        )
    assembly = built[0] if len(built) == 1 else Compound(children=built)
    assembly.label = str(design.get("name") or "assembly")
    return assembly, parameters, names, solid_names, primitive_count


def _name_interferences(interference: dict[str, Any], solid_names: list[str]) -> None:
    """Attach stable component labels to pairwise solid findings in place."""
    for item in interference.get("interferences") or []:
        left, right = int(item["solid_a"]), int(item["solid_b"])
        if left < len(solid_names):
            item["component_a"] = solid_names[left]
        if right < len(solid_names):
            item["component_b"] = solid_names[right]


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
    # text-to-cad's validity contract is explicitly per leaf occurrence. Passing
    # an assembly Compound here made the BOP self-intersection checker treat
    # ordinary coincident mating faces as a self-intersecting *single* body.
    # Validate each solid independently and keep assembly interference separate.
    per_solid = [] if mesh else [check_occurrence_shape(solid.wrapped) for solid in solids]
    reasons: list[str] = []
    for index, finding in enumerate(per_solid):
        for reason in finding.get("reasons") or []:
            labelled = f"solid[{index}]:{reason}"
            if labelled not in reasons:
                reasons.append(labelled)
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
            "volumes": [float(solid.volume) for solid in solids],
            "scope": "per-solid",
        },
    }
    if mesh:
        result["mesh_count"] = 1
    return result


def _assembly_interference(shape, *, max_solids: int = 64,
                           tolerance: float = 1e-5) -> dict[str, Any]:
    """Bounded pairwise overlap check; touching faces are not interference."""
    solids = list(shape.solids())
    if len(solids) < 2:
        return {"checked": True, "pair_count": 0, "interferences": []}
    pair_count = len(solids) * (len(solids) - 1) // 2
    if len(solids) > max_solids:
        return {
            "checked": False,
            "pair_count": pair_count,
            "reason": f"skipped above bounded {max_solids}-solid interference limit",
            "interferences": [],
        }
    findings = []
    boxes = [solid.bounding_box() for solid in solids]
    for left in range(len(solids)):
        a = boxes[left]
        for right in range(left + 1, len(solids)):
            b = boxes[right]
            separated = (
                a.max.X <= b.min.X or b.max.X <= a.min.X
                or a.max.Y <= b.min.Y or b.max.Y <= a.min.Y
                or a.max.Z <= b.min.Z or b.max.Z <= a.min.Z
            )
            if separated:
                continue
            common = solids[left].intersect(solids[right])
            overlap = sum(float(item.volume) for item in common.solids()) if common else 0.0
            if overlap > tolerance:
                findings.append({"solid_a": left, "solid_b": right, "volume": overlap})
                if len(findings) >= 50:
                    return {
                        "checked": True, "pair_count": pair_count,
                        "interferences": findings, "truncated": True,
                    }
    return {"checked": True, "pair_count": pair_count, "interferences": findings}


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
    for key in ("input", "output", "source", "design", "params", "report", "preview"):
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

    if action in {"generate", "generate_design", "validate"}:
        solid_names: list[str] = []
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
        elif action == "generate_design":
            design_path = Path(request["design"]).resolve()
            design = json.loads(design_path.read_text(encoding="utf-8"))
            if not isinstance(design, dict):
                raise TypeError("CAD design must be a JSON object")
            output = Path(request["output"]).resolve()
            shape, parameters, component_names, solid_names, primitive_count = _build_design(design)
            _write_shape(shape, output)
        else:
            output = Path(request["input"]).resolve()

        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(f"STEP artifact was not created: {output}")
        shape = _load_shape(output)
        result = _metrics(shape)
        if not result["validity"]["ok"]:
            raise RuntimeError("generated STEP failed validation: " + ", ".join(result["validity"]["reasons"]))
        interference = _assembly_interference(shape)
        if solid_names:
            _name_interferences(interference, solid_names)

        if action in {"generate", "generate_design"}:
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
            "design": str(request.get("design") or ""),
            "step": str(output),
            "preview": preview_text,
            "assembly_interference": interference,
            **result,
        }
        if action == "generate_design":
            report_payload["component_names"] = sorted(component_names)
            report_payload["component_count"] = len(component_names)
            report_payload["primitive_count"] = primitive_count
            report_payload["parameters"] = parameters
            report_payload["solid_names"] = solid_names
        _write_report(report, report_payload)
        if action in {"generate", "generate_design"} and interference.get("interferences"):
            pairs = interference["interferences"]
            summaries = []
            for item in pairs[:20]:
                left = item.get("component_a") or f"solid[{item['solid_a']}]"
                right = item.get("component_b") or f"solid[{item['solid_b']}]"
                summaries.append(
                    f"{left} vs {right} ({float(item['volume']):.6g} mm^3)"
                )
            summary = ", ".join(summaries)
            return {
                "ok": False, "error_code": "assembly_interference", "retryable": True,
                "error": "overlapping assembly solids detected: " + summary,
                "report": str(report), "preview": preview_text,
                "assembly_interference": interference, **result,
            }
        return {
            "ok": True, "report": str(report), "preview": preview_text,
            "assembly_interference": report_payload["assembly_interference"],
            **({
                "component_names": sorted(component_names),
                "component_count": len(component_names),
                "primitive_count": primitive_count,
            } if action == "generate_design" else {}),
            **result,
        }

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
        message = str(exc)
        lower = message.lower()
        if "syntax" in lower or "constructor arguments" in lower or "expression" in lower:
            code, retryable = "invalid_design", True
        elif "validation" in lower or "topology" in lower or "solid" in lower:
            code, retryable = "invalid_geometry", True
        elif "timed out" in lower or "lock" in lower:
            code, retryable = "runtime_timeout", True
        else:
            code, retryable = "cad_runtime_error", False
        result = {
            "ok": False, "error": message, "error_code": code,
            "retryable": retryable, "traceback": traceback.format_exc(limit=12),
        }
    print(RESULT_START)
    print(json.dumps(result, ensure_ascii=False))
    print(RESULT_END)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
