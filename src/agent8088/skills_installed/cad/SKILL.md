---
name: cad
description: Create, inspect, validate, render, and convert mechanical CAD with build123d and text-to-cad's STEP-first workflow.
---

# CAD

Agent8088 uses **build123d** as its geometry engine and **text-to-cad/cadgen**
for STEP-first generation, named assemblies, topology validation, inspection,
and snapshot review. Do not write FreeCAD Python and do not run CAD source with
`execute_shell`.

## Choose the deterministic tool first

- One box, cylinder, sphere, cone, or tube: `create_cad_part`.
- Convert an existing supported artifact: `convert_cad`.
- Validate or render an existing STEP: `validate_cad_model`.
- Any part with features, an assembly, or a parameterized design:
  `generate_cad_model`.

`generate_cad_model` is the normal path for serious CAD. It retains the Python
source and JSON parameters, generates STEP, exports requested secondary formats,
reopens and validates the geometry, and renders an isometric preview before it
can report success.

## CAD brief before source

Before calling the tool, establish internally:

1. Units (millimetres unless explicitly stated otherwise).
2. Primary axes and origin. Honour an axis the user specified.
3. Named components and expected solid count.
4. Exposed parameters and derived dimensions.
5. Fits, clearances, wall thicknesses, and tolerances.
6. Expected overall bounding-box range.
7. Required formats.

Do not ask a question when the user explicitly asked you to make reasonable
engineering assumptions. Record those assumptions in the final response.

## Source contract

Pass a `.step` filename, a JSON parameter object, and Python source defining
exactly one entry point:

```python
from build123d import *

def gen_step():
    body = Box(PARAMS["length"], PARAMS["width"], PARAMS["height"])
    body.label = "body"
    return body
```

The tool injects `PARAMS` from the provided JSON. Never redefine it. `gen_step`
takes no arguments and returns a build123d `Shape` or a labeled `Compound`.

Allowed imports are build123d, math, dataclasses, and typing. File IO,
network access, process execution, dynamic imports, private/dunder access, and
calls such as `open`, `eval`, or `exec` are rejected. The tool owns every export;
the generator only constructs and returns geometry.

## Modeling order

Use this order unless the geometry requires otherwise:

1. Base solids.
2. Major additions and fusions.
3. Major subtractions and holes.
4. Shells and wall thickness.
5. Repeated features.
6. Fillets and chamfers last.
7. Component placement and labeled compound assembly.

Apply realistic slip-fit clearance explicitly rather than relying on coincident
surfaces. Avoid tangential booleans; extend cutting tools beyond the target.
Remember that build123d primitives are centered on some axes by default. Set
`align=(Align.MIN, Align.MIN, Align.MIN)` when dimensions and positions are
specified from a lower-corner datum; do not compensate later for an accidental
centered origin.

## Assemblies

Keep every requested component as a separate labeled solid. Use `Location`,
`Pos`, and `Rot` for placement and return a `Compound` with labeled children.
Do not fuse an assembly unless the user asks for one printable body.

```python
from build123d import *

def gen_step():
    base = Box(PARAMS["base_x"], PARAMS["base_y"], PARAMS["base_z"])
    base.label = "base"
    post = Pos(0, 0, PARAMS["base_z"]) * Cylinder(
        PARAMS["post_radius"], PARAMS["post_height"]
    )
    post.label = "post"
    assembly = Compound(children=[base, post])
    assembly.label = "assembly"
    return assembly
```

For assemblies, use labeled build123d `Compound` children and explicit
`Location` transforms. text-to-cad/cadgen consumes and verifies the resulting
STEP outside the generator; generator source does not import cadgen directly.

## Validation and repair

Success requires all requested files plus a report and preview. Pay attention to
the returned bounding box, solid count, volume, and validity findings.

When generation fails:

- Boolean failure: enlarge or offset the tool so faces cross instead of touch.
- Fillet failure: reduce the radius or filter the exact intended edges.
- Loft failure: use compatible profiles and consistent orientation.
- Wrong placement: correct the local coordinate frame rather than adding a
  compensating transform at the end.
- Wrong solid count: inspect whether parts were accidentally fused or omitted.
- Wrong axis: repair the source; do not merely rotate the preview.

Never claim success from a Python exit code. The CAD tools make disk artifacts,
reopen them, validate BREP topology, and render a snapshot; report their actual
result.

## Outputs

STEP is the canonical portable CAD artifact. Supported secondary outputs are
STL, 3MF, GLB, and BREP. Native `.FCStd` feature-tree output is not
provided by this backend. If requested, explain that the validated STEP can be
opened in FreeCAD but is not a native PartDesign history.

The normal advanced-model bundle contains:

- `<name>.step.py` — retained parametric build123d source.
- `<name>.params.json` — exposed parameters.
- `<name>.step` — canonical BREP model.
- `<name>.preview.png` — deterministic isometric review image.
- `<name>.report.json` — dimensions and validity evidence.
- Requested secondary exports.

## Upstream basis

The workflow is adapted from earthtojake/text-to-cad's CAD skill and pinned
cadgen runtime. Geometry is produced by gumyr/build123d. Their license and
version notices ship with Agent8088's CAD runtime assets.
