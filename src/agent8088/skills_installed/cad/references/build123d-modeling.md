# build123d modeling patterns (Agent8088 port)

Read this file when writing or repairing build123d Python source for
`generate_cad_model`. Ported from earthtojake/text-to-cad's CAD skill (MIT);
tool names adapted to Agent8088's sandboxed CAD tools.

## Modeling objective

Create a valid STEP-ready BREP model, not a visual mesh. Prefer closed solids,
explicit labels, and stable parametric dimensions. Define `gen_step()` taking no
arguments and returning a `Shape` or labeled `Compound`. Agent8088's worker owns
every export; the generator only constructs and returns geometry. Allowed
imports: build123d, math, dataclasses, typing. File IO, network access, process
execution, dynamic imports, private/dunder access, and calls such as `open`,
`eval`, or `exec` are rejected by the AST validator.

## Design strategy

Decide how the part is constructed before writing geometry code:

- **Choose the construction that makes the spec's dimensions direct parameters.**
  Profile-driven shapes get one closed sketch plus `extrude`/`revolve`/`sweep`/`loft`;
  block-and-feature parts get a base solid plus subtractive features. Prefer
  whichever construction lets the user's controlling dimensions appear as named
  parameters instead of derived values.
- **Decide part vs assembly before modeling.** Bodies that are separately
  manufactured, purchased, or movable belong as separate labeled solids in a
  `Compound`; monolithic manufacturing intent gets a single fused solid. Every
  requested component must remain its own named solid.
- **Pick the origin and orientation from the functional datum before sculpting.**
  Model on the mating interface, mounting plane, or symmetry axis.
- **Order operations so fragile steps come last and failures localize.** Base
  solid → major additions → subtractive features → shell → through-wall holes →
  fillets and chamfers last. Fillets are the most failure-prone operation and
  every boolean invalidates selectors, so postpone them. Structure the source so
  each feature is a named step — a per-feature helper function or a distinct
  intermediate variable — so a failed operation points at exactly one feature
  and a parameter change touches one obvious place.
- **Overshoot boolean tools.** Extend cutting tools past the faces they enter
  and exit; for through-cuts, go roughly 1 mm beyond both faces. Coincident or
  coplanar tool/target faces are a classic kernel failure. Cut repeated or
  patterned features in one combined operation.
- **Clearance is a parameter, not an eyeball.** A pin fits a hole when
  `hole_radius = pin_radius + pin_clearance`. Model rotating/sliding pairs with
  explicit clearance derived from named parameters; never rely on coincident
  surfaces, and never "leave a little gap" by hand-tuned offsets.
- **Contact vs interference.** Separate solids may touch (shared mating face,
  zero-volume contact) or sit with deliberate clearance. They must never
  volumetrically overlap. Ribs, gussets, and bosses that visually merge into
  their parent should be fused INTO that parent's solid (one named component)
  rather than left as overlapping siblings. If two named solids must genuinely
  interpenetrate (press-fit, pin through a bore), the request should declare
  them via allowed_contact (see the CAD skill); otherwise the interference
  check will fail the build.
- **Sanity-check proportions before generating.** Compare the expected bounding
  box against the real-world object, wall thickness against overall size, and
  feature positions against edges and neighboring features. Order-of-magnitude
  and collision errors pass geometric validation but fail visual review.

## Mirrored components

Left/right pairs (fingers, links, supports) come from one builder function
called twice with a sign flip, or from `mirror()` of the finished solid about
the assembly's symmetry plane. Build at the origin, mirror, then translate —
mirroring a point list reverses winding (see sketch algebra below). Keep both
copies as separate labeled children: `LeftFinger`, `RightFinger`.

## Topology stack

Think in this order:

```text
Vertex → Edge → Wire → Face → Shell → Solid → Compound
```

For normal STEP output, return one of:

- a valid `Solid`
- a compound of valid solids (labeled children)

Avoid returning loose wires, open faces, or construction surfaces unless the
user explicitly requested them.

## Parameters first

Put meaningful dimensions in named variables (or in `PARAMS` when the request
defines exposed parameters). Avoid burying important numbers inside geometry
calls.

## Coordinate system

Declare or comment the convention:

```text
Origin: center of primary part or chosen mating datum
XY: main base/sketch plane
+Z: up/extrusion direction
```

Use `Location`, `Plane`, and `Axis` intentionally.

## Selection practices

Avoid fragile topology order when possible. Select by:

- axis or normal
- location or bounding position
- plane grouping
- feature intent
- stable construction plane

For source operations, prefer robust selectors such as top/bottom by axis or
position rather than arbitrary list indexes.

## Labels and assemblies

Label every exported part with native build123d labels (`shape.label = "name"`).
Return a `Compound(children=[...])` with one labeled child per requested
component. Do not prefix labels with topology categories like assembly,
component, feature, datum, mate, or hardware. Use labels for intent topology
cannot infer: role, placement, interface, repetition, or mating purpose.

For repeated parts, keep occurrence labels explicit: `m3_screw:front_left`,
`m3_screw:rear_right`.

## Fillets and chamfers: degrade, never die

Wrap each fillet/chamfer in try/except. On failure, retry with a smaller
radius once, then continue WITHOUT the cosmetic operation — a missing fillet
is a cosmetic regression; a failed build is a lost model. Report which
operations degraded in the final answer.

```python
def safe_fillet(shape, edges, radius):
    try:
        return shape.fillet(radius, edges)
    except Exception:
        try:
            return shape.fillet(radius * 0.5, edges)
        except Exception:
            return shape  # degraded: report in the final answer
```

Do not 3D-chamfer tangent chains or multi-arc outlines — bake the bevel into
the extruded/lofted section profile instead when possible.

## Multi-tool booleans: one list operation

Never accumulate boolean tools pairwise — `body - a - b - c` re-runs the whole
intersection network per step and decays O(n²). Pass every tool in one list
operand: `body - [a, b, c, ...]`. Keep tools mutually disjoint where possible;
split tool families into staged subtracts when one batch returns slivers.

## Validity is not positive volume

`Shape.is_valid()` can return True for a shell with a large negative volume —
an inverted orientation. Check both:

```python
def is_valid_shape(shape):
    return shape is not None and shape.is_valid() and shape.volume > 0.0
```

Agent8088's worker already revalidates every solid after export; gate your own
intermediate booleans with the same thinking rather than trusting the last
operation.

## `.located()` SETS the placement; `.moved()` composes with it

`Shape.rotate()` returns a rotated copy. Placing that copy with
`.located(Location(pos))` throws the rotation away: `located` assigns an
ABSOLUTE location. `.moved()` composes. When a shape already carries a
transform, reach for `.moved()`, or build one `Location(position, rotation)`
and `.located()` that.

## align traps

- build123d primitives are centered by default on some axes. Set
  `align=(Align.MIN, Align.MIN, Align.MIN)` when dimensions and positions are
  specified from a lower-corner datum; do not compensate later for an
  accidental centered origin.
- `align=(None, None, None)` is the raw OCC datum, not "centered": `Box` sits
  with its CORNER at the origin, `Cylinder` base at z=0. Default alignment IS
  centered; reserve `align=None` for when the raw datum is genuinely wanted.

## Common failure modes

- Fillet radius larger than local edge geometry → use the safe_fillet ladder.
- Open sketch profile produces invalid or missing face.
- Loft whose SECTION WIRE self-intersects; bisect by lofting adjacent pairs.
- Smooth `loft()` failing with `BRep_API: command not done` → try
  `loft(..., ruled=True)`.
- `solid += helper()` where the helper returns a list → accumulator becomes a
  `ShapeList`; fuse with explicit geometry instead.
- Face selector changes after a boolean or fillet → re-select after every
  boolean.
- Part origin is arbitrary and later alignment checks become ambiguous.
- Near-tangent booleans silently drop material → build shallow domes as one
  revolved profile instead of tangent boolean stacks.

Use the repair-loop reference when generation or validation fails.