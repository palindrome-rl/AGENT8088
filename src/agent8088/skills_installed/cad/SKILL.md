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
- Open an existing or generated artifact for interactive visual review:
  `open_cad_viewer`.
- Any part with features, an assembly, or a parameterized design expressible
  with boxes, cylinders, spheres, cones, tubes, placements, fusions and cuts:
  `generate_cad_design` (preferred).
- Geometry requiring lofts, sweeps, sketches, selectors, or other advanced
  build123d operations: `generate_cad_model` (escape hatch).

Both complex-model tools generate STEP, reopen and validate every solid
independently, check bounded assembly interference, and render an isometric
preview before reporting success. Secondary formats are emitted only after the
canonical STEP passes those checks.
`generate_cad_design` retains a type-checked `.design.json` plus parameters and
does not execute model-authored Python, so use it whenever its schema fits.

After successful generation or modification, call `open_cad_viewer` with the
canonical STEP unless the user explicitly declines interactive review. The PNG
snapshot is deterministic evidence for automated verification; the Viewer is
the human review surface for assembly trees, selection, measurement, clipping,
exploded layouts, and annotations. Neither replaces topology/interference checks.

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

## Declarative design contract (preferred)

Use a bare filename such as `house.step`; Agent8088 resolves it to its artifacts
directory. Never guess `C:/artifacts`, call a shell to discover the current
directory, or write a separate script.

The tool receives `design` as a structured object (not JSON text embedded inside
another string). The schema has millimetre units, editable parameters, uniquely
named components, and request-derived verification checks. Each component fuses
its `add` primitives, then applies its `cut` primitives. Numeric fields may be
arithmetic expressions referencing parameters. Every primitive supports `at`,
`rotate`, and a compact `placements` array. Unknown or misspelled fields are
rejected rather than silently ignored.

```json
{
  "schema_version": 1,
  "name": "bracket",
  "units": "mm",
  "parameters": {"length": 80, "width": 50, "height": 8, "hole_r": 3.4},
  "components": [{
    "name": "plate",
    "add": [{"type": "box", "size": ["length", "width", "height"]}],
    "cut": [{
      "type": "cylinder", "radius": "hole_r", "height": "height + 2",
      "at": [15, 15, -1], "placements": [[0, 0, 0], [50, 0, 0]]
    }]
  }],
  "verification": {
    "tolerance": 0.05,
    "overall_bounding_box": {"size": ["length", "width", "height"]},
    "solid_count": 1,
    "component_count": 1,
    "components": {
      "plate": {
        "solid_count": 1,
        "bounding_box": {"size": ["length", "width", "height"]}
      }
    }
  }
}
```

Primitive-specific fields are: box `size`; cylinder `radius,height`; sphere
`radius`; cone `radius1,radius2,height`; tube
`outer_radius,inner_radius,height`. Use separate named components when the user
wants separate solids. Avoid overlapping solids; exact touching faces are valid
mating contact and are not misclassified as self-intersection.

`verification` is mandatory in model-visible tool calls. Derive it from the
user's brief; never invent a looser target merely to make a build pass. It may
check the overall `size`, `min`, and/or `max`, exact total solid/component counts,
and the same bounding-box/solid-count facts for named components. A mismatch is
a retryable generation failure, and secondary exports are withheld until the
canonical STEP matches.

## Python source contract (advanced escape hatch)

Pass a `.step` filename, a structured parameter object, request-derived
`verification`, and Python source defining exactly one entry point:

```python
from build123d import *

def gen_step():
    body = Box(PARAMS["length"], PARAMS["width"], PARAMS["height"])
    body.label = "body"
    return body
```

The tool injects `PARAMS` from the provided JSON. Never redefine it. `gen_step`
takes no arguments and returns a build123d `Shape` or a labeled `Compound`.
Advanced-model verification supports expected overall bounding-box `size`,
`min`, and `max`, an absolute tolerance, and total solid count. Secondary
exports are withheld if the generated STEP misses those targets.

Keep the generator compact. Use small helper functions and loops for repeated
features such as windows, holes, columns, fasteners, or floor elements instead
of emitting nearly identical construction statements for every instance. Make
one complete `generate_cad_model` call; do not narrate the source before calling.

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
centered origin. Translate geometry with `Pos(x, y, z) * shape`. If a full
`Location` is required, its position is one tuple: `Location((x, y, z))`.
`Location(x, y, z)` and `Location(z=value)` are invalid build123d calls.

## Assemblies

Keep every requested component as a separate labeled solid. Prefer `Pos` and
`Rot` for placement and return a `Compound` with labeled children.
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

When generation fails, repair the named field or component once. Agent8088 stops
after two failed generation attempts in one turn instead of spending the whole
time/token budget on variants.

- Invalid design/expression: correct only the named schema field.
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

## Interactive visual verification

Use the managed Viewer only through `open_cad_viewer`; never start its Python
server, Node tooling, or a browser from generated code. It binds to loopback and
opens only the authorized artifact directory. For STEP, verify labels and part
structure in the assembly tree, hide/show major components, inspect an exploded
layout, and use clipping where internal clearances matter. Mesh measurements are
vertex-based approximations; use STEP geometry/report values for authoritative
dimensions. If the Viewer cannot start, preserve and report the verified STEP,
JSON report, and PNG rather than regenerating otherwise valid geometry.

## Outputs

STEP is the canonical portable CAD artifact. Supported secondary outputs are
STL, 3MF, GLB, and BREP. Native `.FCStd` feature-tree output is not
provided by this backend. If requested, explain that the validated STEP can be
opened in FreeCAD but is not a native PartDesign history.

The normal advanced-model bundle contains:

- `<name>.design.json` - retained declarative design (preferred workflow), or
  `<name>.step.py` - retained parametric build123d source (advanced workflow).
- `<name>.params.json` — exposed parameters.
- `<name>.step` — canonical BREP model.
- `<name>.preview.png` — deterministic isometric review image.
- `<name>.report.json` — dimensions and validity evidence.
- Requested secondary exports.

## Upstream basis

The workflow is adapted from earthtojake/text-to-cad's CAD skill and pinned
cadgen runtime. Geometry is produced by gumyr/build123d. Their license and
version notices ship with Agent8088's CAD runtime assets.
