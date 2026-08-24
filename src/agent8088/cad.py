"""CAD inspection, conversion, and generation via FreeCAD headless.

This is the CAD analog of `documents.py` and deliberately mirrors its
conventions: `extract_info` returns None for extensions it doesn't handle (so
the caller falls through to normal reading), the write-side functions never
raise and instead return a plain-language string a tool caller can hand back
directly, and every subprocess result is verified against the real artifact
on disk rather than trusted from exit code or stdout.

Unlike `documents.py`, there is no dependency-free path: STEP/IGES require
OpenCascade, which only FreeCAD's own Python environment (bundled with
`freecadcmd`) provides. Every operation here shells out to it.

FreeCAD's exact install layout and its exit-code behaviour on a script
exception could not be verified while writing this (see
`docs/superpowers/specs/2026-08-24-cad-freecad-design.md`, "Open items") —
FreeCAD's wiki was behind bot protection. Treat `freecadcmd` as an untrusted
black box: never believe it succeeded without checking the artifact it was
supposed to produce.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .documents import _readable_or_reason

# CAD files run much larger than office documents (a STEP assembly can be
# hundreds of MB), so the guard here is far more generous than
# MAX_DOCUMENT_BYTES in documents.py.
MAX_CAD_BYTES = 200 * 1024 * 1024

CAD_EXTENSIONS = (
    ".fcstd", ".step", ".stp", ".iges", ".igs", ".stl", ".obj", ".brep", ".dxf",
)

# PDF is deliberately absent. Exporting a 3D model to PDF means generating a
# TechDraw drawing — template, projection direction, scale — not a format
# conversion, and the naive page+view export that looks like one produces an
# empty or broken sheet. Claiming a target that probably fails is worse than
# not claiming it; add it only once it can be verified against a real install.
CONVERTIBLE_CAD_TARGETS = ("step", "stl", "iges", "obj", "brep", "dxf")

CAD_PRIMITIVES = ("box", "cylinder", "sphere", "cone", "tube")

# The exe name and portable/installer layout were unverified when this was
# written (see module docstring) — confirmed on a real install afterward:
# freecadcmd.exe (lowercase) at %LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\. The
# official installer defaults to that per-user, no-elevation location, not
# Program Files — install.ps1's WinGet path was never actually exercised, so
# both roots are checked rather than trusting either guess alone. Both
# casings are kept for a WinGet/portable layout that may still land in
# Program Files. AGENT8088_FREECAD lets a user who extracted a portable 7z
# anywhere point straight at it without editing code.
def _freecad_candidates():
    import os
    names = ("freecadcmd.exe", "FreeCADCmd.exe")
    dirs = [
        r"C:\Program Files\FreeCAD 1.1\bin",
        r"C:\Program Files\FreeCAD\bin",
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        dirs += [
            str(Path(local_app_data) / "Programs" / "FreeCAD 1.1" / "bin"),
            str(Path(local_app_data) / "Programs" / "FreeCAD" / "bin"),
        ]
    return tuple(str(Path(d) / n) for d in dirs for n in names)


FREECAD_INSTALL_PATHS = _freecad_candidates()


def freecad_executable() -> str | None:
    """Find freecadcmd, or None. Checked fresh every call — mirrors
    `documents._soffice_executable()`: install is best-effort and can have
    failed, been skipped, or (for FreeCAD) never been attempted by the
    installer at all yet."""
    import os
    import shutil

    override = os.environ.get("AGENT8088_FREECAD")
    if override and Path(override).exists():
        return override

    for name in ("freecadcmd", "FreeCADCmd"):
        found = shutil.which(name)
        if found:
            return found

    for candidate in FREECAD_INSTALL_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


_NOT_INSTALLED_MESSAGE = (
    "FreeCAD is not installed, so CAD support is unavailable. Install it with: "
    "winget install FreeCAD.FreeCAD (or rerun the Agent8088 installer), or set "
    "AGENT8088_FREECAD to the full path of freecadcmd.exe if it's already "
    "installed in a non-standard location, then try again."
)

# Dispatches how a FreeCAD script should open each source extension. .fcstd is
# a native document; everything else is imported into a fresh document via the
# module that understands that format. Guessed from FreeCAD's documented
# Python API surface, not verified against a real install (see module
# docstring) — a wrong import call here fails safely, because every caller
# checks the resulting artifact on disk/stdout rather than trusting this ran.
def _py_literal(value) -> str:
    """A path as a safe Python string literal for the generated script.

    json.dumps, not an f-string with manually escaped backslashes: the path
    reaches here from a model-supplied filename, and escaping backslashes
    alone leaves a quote or a newline free to close the literal and append
    arbitrary code to a script that then runs unsandboxed. JSON's string
    grammar escapes quotes, backslashes and control characters, and the
    result is a valid Python literal.
    """
    return json.dumps(str(value))


def _open_script_lines(src: Path) -> list[str]:
    ext = src.suffix.lower()
    src_literal = _py_literal(src)
    if ext == ".fcstd":
        return [f"doc = FreeCAD.openDocument({src_literal})"]
    if ext in (".stl", ".obj"):
        return [
            'doc = FreeCAD.newDocument("agent8088_cad")',
            "import Mesh",
            f"Mesh.insert({src_literal}, doc.Name)",
        ]
    if ext == ".dxf":
        return [
            'doc = FreeCAD.newDocument("agent8088_cad")',
            "import importDXF",
            f"importDXF.insert({src_literal}, doc.Name)",
        ]
    # .step/.stp/.iges/.igs/.brep — all OpenCascade formats Part.insert reads.
    return [
        'doc = FreeCAD.newDocument("agent8088_cad")',
        "import Part",
        f"Part.insert({src_literal}, doc.Name)",
    ]


def _run_freecad_script(freecad: str, script: str, timeout: int):
    """Write `script` to a temp file, run it under freecadcmd, clean up.
    Inline `-c` code is not used: quoting a multi-line script through a shell
    is fragile, a temp file is not."""
    import subprocess
    import tempfile
    import os

    fd, script_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        return subprocess.run(
            [freecad, script_path],
            capture_output=True, text=True, timeout=timeout,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def extract_info(path, max_bytes: int = MAX_CAD_BYTES):
    """Return a text summary of a CAD file, or None if `path`'s extension
    isn't CAD, so the caller falls through to normal reading. Same contract
    as `documents.extract_text`."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in CAD_EXTENSIONS:
        return None

    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"CAD file is too large to inspect (limit: {max_bytes} bytes): {path}"
        )

    unreadable = _readable_or_reason(path)
    if unreadable:
        return unreadable

    freecad = freecad_executable()
    if not freecad:
        return _NOT_INSTALLED_MESSAGE

    if not path.exists():
        return f"Cannot inspect: {path} does not exist."

    script = "\n".join([
        "import FreeCAD, json",
        *_open_script_lines(path),
        "objects = []",
        "bbox = None",
        "total_volume = 0.0",
        "total_area = 0.0",
        "for obj in doc.Objects:",
        "    objects.append({'name': obj.Name, 'label': getattr(obj, 'Label', obj.Name), 'type': obj.TypeId})",
        "    shape = getattr(obj, 'Shape', None)",
        "    if shape is not None and not shape.isNull():",
        "        try:",
        "            total_volume += shape.Volume",
        "            total_area += shape.Area",
        "        except Exception:",
        "            pass",
        "        try:",
        "            bb = shape.BoundBox",
        "            bbox = bb if bbox is None else (bbox.add(bb) or bbox)",
        "        except Exception:",
        "            pass",
        "result = {'objects': objects, 'volume': total_volume, 'area': total_area}",
        "if bbox is not None:",
        "    result['bounding_box'] = {'length': bbox.XLength, 'width': bbox.YLength, 'height': bbox.ZLength}",
        "print('AGENT8088_CAD_JSON_START')",
        "print(json.dumps(result))",
        "print('AGENT8088_CAD_JSON_END')",
    ])

    try:
        result = _run_freecad_script(freecad, script, timeout=120)
    except Exception as exc:  # subprocess.TimeoutExpired and friends
        import subprocess
        if isinstance(exc, subprocess.TimeoutExpired):
            return f"FreeCAD timed out after 120s inspecting {path.name}."
        return f"Could not run FreeCAD: {exc}"

    # freecadcmd may exit 0 even after a script exception (unverified, see
    # module docstring) — the only trustworthy signal is whether the expected
    # JSON actually appears on stdout, not the exit code.
    stdout = result.stdout or ""
    if "AGENT8088_CAD_JSON_START" not in stdout:
        detail = (result.stderr or stdout or "no output from freecadcmd").strip()
        return f"Could not inspect {path.name}: FreeCAD said: {detail[:500]}"

    import json
    payload = stdout.split("AGENT8088_CAD_JSON_START", 1)[1]
    payload = payload.split("AGENT8088_CAD_JSON_END", 1)[0].strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return f"Could not parse FreeCAD output for {path.name}: {exc}"

    lines = [f"CAD file: {path.name}", f"Objects: {len(data.get('objects', []))}"]
    for obj in data.get("objects", []):
        lines.append(f"  - {obj.get('label', obj.get('name'))} ({obj.get('type')})")
    bbox = data.get("bounding_box")
    if bbox:
        lines.append(
            f"Bounding box: {bbox.get('length'):.3f} x {bbox.get('width'):.3f} "
            f"x {bbox.get('height'):.3f} mm (L x W x H)"
        )
    if data.get("volume"):
        lines.append(f"Volume: {data['volume']:.3f} mm^3")
    if data.get("area"):
        lines.append(f"Surface area: {data['area']:.3f} mm^2")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def _export_script_lines(target: str, output: Path) -> list[str]:
    """Build the export half of a conversion script for `target`. Export API
    guessed from FreeCAD's documented module layout (Part.export for
    OpenCascade formats, Mesh.export for STL/OBJ) — unverified, see module
    docstring. A wrong guess here fails safely: convert_cad only reports
    success once the output file actually exists on disk."""
    out_literal = _py_literal(output)
    objs = "[o for o in doc.Objects if hasattr(o, 'Shape') and o.Shape and not o.Shape.isNull()]"
    if target in ("step", "iges", "brep"):
        return [
            "import Part",
            f"shapes = {objs}",
            "if not shapes:",
            "    raise RuntimeError('no exportable shapes in document')",
            f"Part.export(shapes, {out_literal})",
        ]
    if target in ("stl", "obj"):
        return [
            "import Mesh, MeshPart",
            f"shapes = {objs}",
            "mesh_objs = [o for o in doc.Objects if o.TypeId.startswith('Mesh::')]",
            "if shapes:",
            "    meshes = [MeshPart.meshFromShape(Shape=s.Shape, LinearDeflection=0.1) for s in shapes]",
            "    combined = meshes[0]",
            "    for m in meshes[1:]:",
            "        combined.addMesh(m)",
            f"    combined.write({out_literal})",
            "elif mesh_objs:",
            f"    Mesh.export(mesh_objs, {out_literal})",
            "else:",
            "    raise RuntimeError('no exportable geometry in document')",
        ]
    if target == "dxf":
        return [
            "import importDXF",
            f"shapes = {objs}",
            "if not shapes:",
            "    raise RuntimeError('no exportable shapes in document')",
            f"importDXF.export(shapes, {out_literal})",
        ]
    # Every target in CONVERTIBLE_CAD_TARGETS is handled above; the format was
    # validated by convert_cad before reaching here.
    raise ValueError(f"no exporter for target format: {target}")


def convert_cad(path, target_format: str, timeout: int = 180) -> str:
    """Convert `path` to `target_format` via freecadcmd, in place (same
    directory, same basename, new extension). Returns a summary or a
    plain-language reason it didn't happen — never raises, so a tool caller
    can return this string directly."""
    path = Path(path)
    target_format = (target_format or "").strip().lower().lstrip(".")
    if target_format not in CONVERTIBLE_CAD_TARGETS:
        return (f"Cannot convert to '{target_format}'. Supported targets: "
                 f"{', '.join(CONVERTIBLE_CAD_TARGETS)}.")

    freecad = freecad_executable()
    if not freecad:
        return _NOT_INSTALLED_MESSAGE

    if not path.exists():
        return f"Cannot convert: {path} does not exist."

    unreadable = _readable_or_reason(path)
    if unreadable:
        return unreadable

    output_path = path.with_suffix("." + target_format)
    script = "\n".join([
        "import FreeCAD",
        *_open_script_lines(path),
        *_export_script_lines(target_format, output_path),
    ])

    import subprocess
    try:
        result = _run_freecad_script(freecad, script, timeout=timeout)
    except subprocess.TimeoutExpired:
        return (f"FreeCAD timed out after {timeout}s converting {path.name}. "
                 "Cold-start can be slow — try again, or raise the timeout.")

    # As with extract_info: freecadcmd may exit 0 on a script exception, so
    # success is only ever established by checking the artifact on disk.
    if not output_path.exists():
        detail = (result.stderr or result.stdout or "no output from freecadcmd").strip()
        return (f"Conversion failed: {path.name} was not converted to "
                 f"{target_format}. FreeCAD said: {detail[:500]}")

    return f"Converted {path.name} to {output_path.name} ({output_path.stat().st_size} bytes)."


# ---------------------------------------------------------------------------
# Primitive generation
# ---------------------------------------------------------------------------
# One deliberately simple, documented dimension format per shape, since a
# model will send free text and this has to parse it defensively rather than
# assume it's well-formed:
#   box:      "LxWxH"                    e.g. "50x30x10"
#   cylinder: "rRxH"                     e.g. "r10x50"
#   sphere:   "rR"                       e.g. "r10"
#   cone:     "rR1xR2xH"                 e.g. "r10x5x20"  (R1=base, R2=top)
#   tube:     "rOUTERxINNERxH"           e.g. "r10x5x20"  (outer, inner, height)
# Also accepts explicit "key=value,key=value" for anyone who wants to be
# unambiguous about which number is which.
_DIM_KEYS = {
    "box": ("length", "width", "height"),
    "cylinder": ("radius", "height"),
    "sphere": ("radius",),
    "cone": ("radius1", "radius2", "height"),
    "tube": ("outer_radius", "inner_radius", "height"),
}
_KEY_ALIASES = {
    "l": "length", "w": "width", "h": "height", "height": "height",
    "r": "radius", "radius": "radius",
    "r1": "radius1", "radius1": "radius1",
    "r2": "radius2", "radius2": "radius2",
    "outer": "outer_radius", "outer_radius": "outer_radius",
    "inner": "inner_radius", "inner_radius": "inner_radius",
    "length": "length", "width": "width",
}


def _parse_dimensions(shape: str, dimensions: str) -> dict:
    """Parse a dimensions string for `shape`. Raises ValueError with a
    human-readable reason on anything malformed — caught by the caller,
    never surfaced as a traceback."""
    keys = _DIM_KEYS[shape]
    dimensions = (dimensions or "").strip()
    if not dimensions:
        raise ValueError(
            f"No dimensions given for '{shape}'. Expected e.g. "
            f"{'x'.join('N' for _ in keys)} (fields: {', '.join(keys)})."
        )

    if "=" in dimensions:
        values = {}
        for part in dimensions.split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            key = _KEY_ALIASES.get(k.strip().lower())
            if key is None or key not in keys:
                continue
            try:
                values[key] = float(v.strip())
            except ValueError:
                raise ValueError(f"'{v.strip()}' in '{dimensions}' is not a number.")
        missing = [k for k in keys if k not in values]
        if missing:
            raise ValueError(
                f"Missing {', '.join(missing)} for '{shape}' in '{dimensions}'."
            )
        return values

    tokens = re.split(r"[xX]", dimensions)
    tokens = [t.strip().lower().lstrip("r") for t in tokens]  # "r10" -> "10"
    if len(tokens) != len(keys):
        raise ValueError(
            f"'{dimensions}' has {len(tokens)} value(s), '{shape}' needs "
            f"{len(keys)}: {'x'.join('N' for _ in keys)} (fields: {', '.join(keys)})."
        )
    values = {}
    for key, token in zip(keys, tokens):
        try:
            values[key] = float(token)
        except ValueError:
            raise ValueError(f"'{token}' in '{dimensions}' is not a number.")
    return values


def _primitive_script_lines(shape: str, dims: dict) -> list[str]:
    if shape == "box":
        return [
            "import Part",
            f"part = Part.makeBox({dims['length']}, {dims['width']}, {dims['height']})",
        ]
    if shape == "cylinder":
        return [
            "import Part",
            f"part = Part.makeCylinder({dims['radius']}, {dims['height']})",
        ]
    if shape == "sphere":
        return [
            "import Part",
            f"part = Part.makeSphere({dims['radius']})",
        ]
    if shape == "cone":
        return [
            "import Part",
            f"part = Part.makeCone({dims['radius1']}, {dims['radius2']}, {dims['height']})",
        ]
    # tube: outer cylinder with inner cylinder cut out
    return [
        "import Part",
        f"outer = Part.makeCylinder({dims['outer_radius']}, {dims['height']})",
        f"inner = Part.makeCylinder({dims['inner_radius']}, {dims['height']})",
        "part = outer.cut(inner)",
    ]


def create_cad_part(path, shape: str, dimensions: str, timeout: int = 180) -> str:
    """Generate a primitive CAD part (parameters, never freeform code) and
    save it to `path`. Output format is inferred from `path`'s extension and
    must be one of CONVERTIBLE_CAD_TARGETS. Never raises."""
    path = Path(path)
    shape = (shape or "").strip().lower()
    if shape not in CAD_PRIMITIVES:
        return f"Unknown shape '{shape}'. Supported: {', '.join(CAD_PRIMITIVES)}."

    target_format = path.suffix.lower().lstrip(".")
    if target_format not in CONVERTIBLE_CAD_TARGETS:
        return (f"Cannot save as '{path.suffix}'. Supported output formats: "
                 f"{', '.join(CONVERTIBLE_CAD_TARGETS)}.")

    try:
        dims = _parse_dimensions(shape, dimensions)
    except ValueError as exc:
        return str(exc)

    freecad = freecad_executable()
    if not freecad:
        return _NOT_INSTALLED_MESSAGE

    script = "\n".join([
        "import FreeCAD",
        'doc = FreeCAD.newDocument("agent8088_cad")',
        *_primitive_script_lines(shape, dims),
        'obj = doc.addObject("Part::Feature", "Part")',
        "obj.Shape = part",
        "doc.recompute()",
        *_export_script_lines(target_format, path),
    ])

    import subprocess
    try:
        result = _run_freecad_script(freecad, script, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"FreeCAD timed out after {timeout}s generating {path.name}."

    if not path.exists():
        detail = (result.stderr or result.stdout or "no output from freecadcmd").strip()
        return (f"Generation failed: {path.name} was not created. "
                 f"FreeCAD said: {detail[:500]}")

    return f"Created {path.name} ({path.stat().st_size} bytes) — {shape} {dimensions}."


if __name__ == "__main__":
    # Self-check that runs WITHOUT FreeCAD installed: exercises every failure
    # path that doesn't require the real binary. Run with:
    #   python -m agent8088.cad
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # unknown extension -> None
        txt_path = tmp / "t.txt"
        txt_path.write_text("plain")
        assert extract_info(txt_path) is None

        # size guard
        big_path = tmp / "big.step"
        big_path.write_bytes(b"0" * 100)
        try:
            extract_info(big_path, max_bytes=10)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

        # unsupported conversion target
        step_path = tmp / "part.step"
        step_path.write_text("not real step data")
        result = convert_cad(step_path, "docx")
        assert "Supported targets" in result, result

        # source does not exist
        result = convert_cad(tmp / "missing.step", "stl")
        assert "does not exist" in result or "FreeCAD is not installed" in result, result

        # bad shape
        result = create_cad_part(tmp / "out.step", "torus", "10x5")
        assert "Unknown shape" in result, result

        # bad output extension
        result = create_cad_part(tmp / "out.docx", "box", "50x30x10")
        assert "Supported output formats" in result, result

        # dimension parsing: shorthand and key=value, valid and invalid
        assert _parse_dimensions("box", "50x30x10") == {
            "length": 50.0, "width": 30.0, "height": 10.0,
        }
        assert _parse_dimensions("cylinder", "r10x50") == {
            "radius": 10.0, "height": 50.0,
        }
        assert _parse_dimensions("cylinder", "radius=10,height=50") == {
            "radius": 10.0, "height": 50.0,
        }
        try:
            _parse_dimensions("box", "50x30")
            raise AssertionError("expected ValueError for wrong field count")
        except ValueError:
            pass
        try:
            _parse_dimensions("box", "50xNOTANUMBERx10")
            raise AssertionError("expected ValueError for non-numeric field")
        except ValueError:
            pass

        # If FreeCAD genuinely isn't installed on this machine (true in this
        # dev environment), every operation reports that honestly rather than
        # a traceback — the one path that stays fully exercisable here.
        if freecad_executable() is None:
            assert "not installed" in extract_info(step_path) or "Could not read" in extract_info(step_path)
            assert "not installed" in convert_cad(step_path, "stl")
            assert "not installed" in create_cad_part(tmp / "out.step", "box", "50x30x10")

    print("cad.py self-check passed")
