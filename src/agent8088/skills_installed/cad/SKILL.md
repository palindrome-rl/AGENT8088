---
name: cad
description: Create, inspect, validate, render, and convert mechanical CAD with build123d and text-to-cad's STEP-first workflow — from single primitives to full multi-part mechanisms.
---

# CAD

Agent8088 uses **build123d** as its geometry engine and **text-to-cad/cadgen**
for STEP-first generation, named assemblies, topology validation, inspection,
and snapshot review. Do not write FreeCAD Python and do not run CAD source with
`execute_shell`.

## Tool choice — decision tree

1. One box, cylinder, sphere, cone, or tube → `create_cad_part`.
2. Convert an existing supported artifact → `convert_cad`.
3. Validate or render an existing STEP → `validate_cad_model`.
4. Open an existing or generated artifact for interactive review →
   `open_cad_viewer`.
5. Any part or assembly expressible with boxes, cylinders, spheres, cones,
   tubes, placements, fusions and cuts (no fillets, chamfers, mirrors, or
   sketches) → `generate_cad_design` (preferred; type-checked, no
   model-authored Python).
6. Every multi-component, movable, robotic, architectural, or otherwise
   complex assembly → the staged `cad_project_*` workflow (see "Staged
   assembly workflow" below). Required — never put a whole assembly in one
   `generate_cad_model` call.
7. Everything else (a single part) → `generate_cad_model` (full build123d
   Python via one `gen_step()` function). Route here whenever the brief names
   fillets, chamfers, mirrors, serrations, ribs, lofts, sweeps, sketches,
   selectors, gears, threads — or any feature the five declarative primitives
   cannot express. **When in doubt, use `generate_cad_model`**: it is the
   universal single-part path and is always available for CAD requests.

Both complex-model tools generate STEP, export requested secondary formats,
reopen and validate every solid independently, check bounded assembly
interference (honoring any declared allowed_contact pairs), and render an
isometric preview before reporting success. After successful generation, call
`open_cad_viewer` with the canonical STEP unless the user explicitly declines
interactive review.

## CAD brief before source

Before calling a tool, establish internally (see
`references/cad-brief.md` for the full template and clarification policy):

1. Units (millimetres unless explicitly stated otherwise).
2. Primary axes, origin, and the functional datum.
3. Named components and expected solid count — one labeled solid per
   requested component.
4. Exposed parameters and derived dimensions.
5. Fits, clearances, wall thicknesses, tolerances. **Clearance is a named
   parameter**: `bore_radius = pin_radius + pin_clearance`, never an eyeballed
   gap.
6. Intended contacts (pin through bore, press-fit, rib fused into parent) to
   declare via `allowed_contact`.
7. Expected overall bounding-box range.
8. Required formats.

Do not ask a question when the user explicitly asked you to make reasonable
engineering assumptions. Record those assumptions in the final response.

## Declarative design contract (generate_cad_design)

Use a bare filename such as `house.step`; Agent8088 resolves it to its
artifacts directory. Never guess `C:/artifacts`, call a shell to discover the
current directory, or write a separate script.

The schema has millimetre units, editable parameters, uniquely named
components, and arithmetic expressions referencing parameters. Every primitive
supports `at`, `rotate`, and a compact `placements` array.

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
  "allowed_contact": [["PivotPins", "LeftPivotSupport"]]
}
```

Primitive-specific fields: box `size`; cylinder `radius,height`; sphere
`radius`; cone `radius1,radius2,height`; tube `outer_radius,inner_radius,
height`. Use separate named components when the user wants separate solids.
Avoid overlapping solids; exact touching faces are valid mating contact and
are not interference. The declarative schema cannot express fillets,
chamfers, mirrored pairs, or sketch-driven geometry — for those, use
`generate_cad_model`.

## Python source contract (generate_cad_model — universal path)

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
takes no arguments and returns a build123d `Shape` or a labeled `Compound`
with one labeled child per requested component.

Keep the generator compact. Use small helper functions and loops for repeated
features instead of emitting nearly identical statements per instance. Make
one complete `generate_cad_model` call; do not narrate the source before
calling.

Allowed imports are build123d, math, dataclasses, and typing. File IO,
network access, process execution, dynamic imports, private/dunder access, and
calls such as `open`, `eval`, or `exec` are rejected. The tool owns every
export; the generator only constructs and returns geometry.

**Read `references/build123d-modeling.md` before writing non-trivial
source.** It carries the hard-won traps: fillet ladders that degrade silently,
`Plane.rotated()` composing in world axes, `.located()` discarding rotations,
align datums, multi-tool boolean batching, inverted solids passing validity.

## Staged assembly workflow (cad_project_*)

Declare the whole assembly in **one** `cad_project_create` call — every part
and every connection between them — instead of discovering it one component
at a time:

- **`parts`**: array of `{name, kind, ...}`. `kind: "custom"` needs a
  `description` and a later `cad_project_add_component` call with real
  build123d source. `kind: "warehouse.fastener"` or `"warehouse.gear"` needs a
  `params` object instead and is built immediately, deterministically, with
  **zero further model turns** — do not write source for these; `create`
  already built them by the time it returns.
- **`mates`**: array of `{type, a, b}` connecting `"PartName.port_name"`
  pairs. `type` is one of `coaxial` (align two axes — a pin in a bore),
  `face_to_face` (coincident faces), `press_fit` (like coaxial, plus
  auto-exempted from interference — this *replaces* `allowed_contact` for the
  assembly workflow: a declared press_fit or gear_mesh mate already says the
  pair is expected to touch), or `gear_mesh` (needs `module`, `teeth_a`,
  `teeth_b` too; positions two gears at their pitch-circle center distance).
- Positions are **never** authored by you. `cad_project_finalize` takes no
  placement argument at all — it computes every part's location from the
  mates you already declared. If a part needs to mate with something, give it
  a named port in `cad_project_add_component`'s `ports` field: `{port_name:
  {at: [x,y,z], axis: [x,y,z]}}`, where `axis` points away from that part's
  own body, toward whatever it will mate with.
- `cad_project_add_component` is bounded to 3 real repair attempts per
  component name. A 4th call after 3 failures is refused — rework the
  approach for that component rather than retrying the same broken one.
- A part connecting to two other parts simultaneously (e.g. a shared housing
  with jaws mating on one side and a scroll mechanism on the other) is fine —
  the assembly only needs to be one valid *static* configuration, not a
  proof that the mechanism can move through its full range.

## Mechanism playbook (moving assemblies)

This is about geometry *within* one `generate_cad_model` call or one
`cad_project_add_component`'s custom part — placing features inside a single
component's own `gen_step()`. Positioning *between* separate parts in a
staged project uses the mates in "Staged assembly workflow" above, not `Pos`/
`Rot` on a whole component.

For grippers, linkages, hinges, and any assembly with pins/pivots (full
detail in `references/positioning.md`):

- Model each moving part at the origin with its pivot axis on a coordinate
  axis; place with `Pos`/`Rot` afterward.
- Derive every position from parameters — pivot positions, link lengths,
  angles — never hand-tuned constants.
- Mirrored left/right parts: one builder function called twice with a sign
  flip, or `mirror()` about the symmetry plane. Keep both as separate labeled
  solids.
- Through-holes sharing an axis (finger pivot, support bore, link eye) are
  built from the SAME parameter.
- Fuse ribs/gussets into their parent solid; do not leave overlapping
  siblings.
- Declare intended contacts (pins through bores, link-on-pin) via
  `allowed_contact` pairs; everything undeclared still fails the interference
  check.
- Fillets and chamfers last, each wrapped in a try/except that degrades
  gracefully (retry at half radius, then skip) instead of failing the build.

## Modeling order

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
specified from a lower-corner datum.

## Validation and repair

Success requires all requested files plus a report and preview. Pay attention
to the returned bounding box, solid count, volume, and validity findings.

Generation attempts are bounded per turn (configurable via
`cad_max_generation_attempts` in config.txt). Each retry must repair the
NAMED failure — a variant shot wastes the budget. Follow
`references/repair-loop.md` to classify the failure and apply the smallest
responsible fix:

- Invalid design/expression: correct only the named schema field.
- Boolean failure: enlarge or offset the tool so faces cross instead of touch.
- Fillet failure: reduce the radius or filter the exact intended edges
  (safe_fillet ladder in the modeling reference).
- Loft failure: use compatible profiles and consistent orientation.
- Wrong placement: correct the local coordinate frame rather than adding a
  compensating transform at the end.
- Wrong solid count: inspect whether parts were accidentally fused or omitted.
- Wrong axis: repair the source; do not merely rotate the preview.
- Interference failure: separate the solids, fuse intruding features into
  their parent, or declare the pair via allowed_contact when genuinely
  intended.

Never claim success from a Python exit code. The CAD tools make disk
artifacts, reopen them, validate BREP topology, and render a snapshot; report
their actual result.

## Interactive visual verification

Use the managed Viewer only through `open_cad_viewer`; never start its Python
server, Node tooling, or a browser from generated code. It binds to loopback
and opens only the authorized artifact directory. For STEP, verify labels and
part structure in the assembly tree, hide/show major components, inspect an
exploded layout, and use clipping where internal clearances matter. Mesh
measurements are vertex-based approximations; use STEP geometry/report values
for authoritative dimensions. If the Viewer cannot start, preserve and report
the verified STEP, JSON report, and PNG rather than regenerating otherwise
valid geometry.

## Outputs

STEP is the canonical portable CAD artifact. Supported secondary outputs are
STL, 3MF, GLB, and BREP. Native `.FCStd` feature-tree output is not provided
by this backend. If requested, explain that the validated STEP can be opened
in FreeCAD but is not a native PartDesign history.

The normal advanced-model bundle contains:

- `<name>.design.json` — retained declarative design (preferred workflow), or
  `<name>.step.py` — retained parametric build123d source (advanced workflow).
- `<name>.params.json` — exposed parameters.
- `<name>.step` — canonical BREP model.
- `<name>.preview.png` — deterministic isometric review image.
- `<name>.report.json` — dimensions and validity evidence.
- Requested secondary exports.

## References (lazy-load per trigger)

- `references/cad-brief.md` — converting prose/images/drawings into a brief.
- `references/build123d-modeling.md` — build123d patterns, traps, labels,
  safe fillets, boolean batching. Read before non-trivial Python source.
- `references/repair-loop.md` — failure classification and smallest fixes.
- `references/positioning.md` — mechanisms, pins, clearance, mirrored parts,
  assembly structure.

## Upstream basis

The workflow is adapted from earthtojake/text-to-cad's CAD skill and the
pinned cadgen runtime (both MIT). Geometry is produced by gumyr/build123d.
Their license and version notices ship with Agent8088's CAD runtime assets.