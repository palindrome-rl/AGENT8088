# Positioning, mechanisms, and mating (Agent8088 port)

Read this file when geometry has mating interfaces, repeated features,
assembly children, axes, datums, or motion. Ported from earthtojake/text-to-cad
(MIT), trimmed to what Agent8088's pinned cadgen 0.4.28 runtime and sandboxed
generator support: source-level build123d joints ARE available inside
`gen_step()`; the upstream `cadgen.assembly.AssemblyHelper` wrapper and CLI
`inspect align/frame/measure` tools are NOT part of Agent8088's runtime, so
positioning is authored with explicit `Location`/`Pos`/`Rot` transforms and
validated through the worker's report (bounding boxes, solid names, pairwise
interference findings).

## Core rule

Positioning is authored in source and validated after generation. Do not
position parts by visually nudging or by editing exported STEP geometry. Use
build123d parameters, local coordinate systems, and `Location` transforms, then
validate through the worker's report and the PNG preview.

## Terminology

- **Mating intent** is the design relationship: flush, centered, coaxial,
  offset, hinge-like, slider-like, or otherwise datum-driven.
- **build123d joints** (`RigidJoint`, `RevoluteJoint`, `LinearJoint`,
  `CylindricalJoint`, `BallJoint`) are source-level objects available inside
  `gen_step()`. `connect_to()` is a one-time source-generation operation that
  repositions the moving part; it is not a persistent constraint in the
  exported STEP.
- **Explicit transforms** (`Pos`, `Rot`, `Location`) place parts directly. Use
  them when only final static placement matters and the transform is
  parameterized.

## Part-local positioning

For each part, define a local coordinate convention before modeling:

```text
- Origin: center, base datum, mounting interface, or functional axis.
- XY plane: main sketch/base plane unless another datum is dominant.
- +Z: extrusion/up direction.
- Named dimensions: offsets, hole spacing, boss spacing, clearances.
- Datum features: mating faces, screw axes, centerlines, locating tabs, rails.
```

Good defaults:

- Symmetric standalone parts: origin at body center.
- Plates: origin at footprint center; thickness along Z.
- Enclosures: origin at footprint center; base/lid mating surfaces controlled
  by Z parameters.
- Shaft/knob/axisymmetric parts: origin on rotational axis.
- Mating adapter plates: origin on the primary mounting datum or the center of
  the bolt pattern.

## Feature placement inside a part

Use named parameters and local coordinates; avoid untraceable placement
constants inside geometry calls.

```python
hole_positions = [
    (-hole_offset_x, -hole_offset_y),
    ( hole_offset_x, -hole_offset_y),
    (-hole_offset_x,  hole_offset_y),
    ( hole_offset_x,  hole_offset_y),
]
```

## Mechanism assemblies (pins, pivots, linkages)

For a moving mechanism (grippers, linkages, hinges):

1. Model each moving part at the ORIGIN with its pivot axis on a coordinate
   axis; place it afterward with `Pos`/`Rot`.
2. Derive every position from parameters: pivot positions, link lengths, and
   angles come from the parameter block, never hand-tuned constants.
3. Clearance is a named parameter. A pin fits its bore when
   `bore_radius = pin_radius + pin_clearance`. Rotating parts keep
   `pin_clearance` to every static surface they sweep past.
4. Pivot holes through a support block must be through-holes along the same
   axis as the finger's pivot hole — model them from the SAME parameter.
5. Keep every requested component a separate labeled solid. Pins crossing a
   support bore and links sharing a pin are intended contacts; declare those
   pairs via allowed_contact so the interference check exempts exactly those
   pairs. Everything undeclared still fails.

```python
finger = Box(finger_length, finger_width, finger_thickness)
finger.label = "LeftFinger"
pivot = Pos(-finger_length / 2 + 8, 0, 0) * Rot(0, 90, 0) * Cylinder(
    pivot_diameter / 2 + pin_clearance, finger_thickness + 2
)
finger = finger - pivot
```

## When to use build123d joints

Use `RigidJoint`/`RevoluteJoint`/`LinearJoint` inside `gen_step()` when
assembly intent is clearer as a relationship between part datums than as a raw
transform (hinge, slider, pin-in-hole). When only final static placement
matters, parameterized explicit `Location` transforms are fine. `connect_to()`
is a source-generation operation: it repositions the moving part for the
generated model; it is not a persistent constraint in the exported STEP file.

## Validation without upstream CLI tooling

Agent8088 validates for you — the worker reopens the exported STEP, checks
every solid's topology and positive volume, runs the bounded pairwise
interference check, and renders an isometric snapshot. To verify placement
yourself in the generator, measure on the geometry before returning:

```python
assert abs(finger.volume) > 0, "degenerate finger"
bb = assembly.bounding_box()
```

Report expected vs actual bounding box in the final answer. Use
`open_cad_viewer` for interactive visual review of assembly structure,
exploded layouts, and clipping. Mesh measurements in the Viewer are
approximations; STEP geometry and the report are authoritative.

## Source-level positioning corrections

When a positioning check fails, fix one of these in source:

- child `Location` translation or rotation
- joint location or axis
- part-local origin convention
- feature offset parameter
- sketch plane / workplane selection
- assembly hierarchy
- symmetric placement signs

Then regenerate. Do not patch the exported STEP.

## Reporting positioning

In the final response, report only checks that actually ran:

```text
Positioning:
- pivot axes modeled through shared parameter, finger/support bores coaxial
- pin clearance 0.3 mm on all pivot pairs (declared allowed_contact)
- overall bbox 100 x 120 x 50 mm (expected 100 x 120 x 50)
```

If a mate or alignment was intended but not checked, say `not checked`; do not
imply success.