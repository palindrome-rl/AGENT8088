# Repair loop (Agent8088 port)

Read this file when a `generate_cad_design` / `generate_cad_model` /
`validate_cad_model` call fails. Ported from earthtojake/text-to-cad's CAD
skill (MIT); tool names adapted to Agent8088's sandboxed CAD tools. Agent8088
caps generation attempts per turn (configurable via
`cad_max_generation_attempts` in config.txt), so each retry must be a genuine
diagnosis, not a variant shot.

## Loop

1. Read the failing tool output.
2. Classify the failure (below).
3. Make the smallest responsible source or design change — repair only the
   named field or component; do not rewrite the whole design.
4. Regenerate.
5. Rerun the failed validation plus any dependent checks.
6. Report remaining risk or deliberate deviations.

## Failure classes and fixes

### Schema/declarative design errors (generate_cad_design)

Likely causes:

- unknown parameter referenced in an expression
- unsupported primitive type (declarative schema: box, cylinder, sphere, cone,
  tube)
- non-positive dimension
- components overlap volumetrically (interference) without an allowed_contact
  declaration
- wrong solid count (parts accidentally fused or omitted)

Fix:

- correct only the named schema field or component
- for interference: separate the solids, fuse the intruding feature into its
  parent's component, or declare the pair via allowed_contact when the overlap
  is a genuine intended contact (pin through bore, press-fit)
- for wrong solid count: check whether parts were accidentally fused or omitted

### Source import or syntax failure (generate_cad_model)

Likely causes:

- invalid Python syntax
- disallowed import (only build123d, math, dataclasses, typing are permitted)
- private/dunder or file-capable method usage (rejected by the AST validator)
- function not named `gen_step()` with no arguments
- `PARAMS` redefined (it is injected; never redefine it)

Fix:

- correct imports and syntax
- ensure `gen_step()` returns the STEP-ready shape or labeled compound
- keep every export in the tool's hands; the generator only constructs geometry

### Multi-section loft: "Failed to create valid loft" / "Recovery failed"

The message names neither the station nor the cause. Two checks, in this order:

1. **Loft increasing PREFIXES** (`faces[:5]`, `[:10]`, `[:20]`, …) to bracket
   where it breaks, and watch the reported volume as well as the exception.
2. **Loft every ADJACENT PAIR.** If every pair succeeds but the full set fails,
   the sections disagree on POINT COUNT. Guarantee a fixed sample count per
   section.

Two silent causes worth ruling out before either:

- **A section that is genuinely disconnected** (two closed regions) produces a
  `Face` that raises nothing and reports a plausible area; only `Face.is_valid`
  is False. End the loft at the last connected station, or bridge the gap in
  the section and cut it back afterwards. (`Face.is_valid` is a PROPERTY —
  calling `f.is_valid()` raises `TypeError: 'bool' object is not callable`.)
- **Samples dropped where a component does not exist** make counts vary station
  to station. Carry a value rather than dropping the sample.

### Boolean failure

Likely causes:

- coincident or coplanar tool/target faces
- near-tangent surfaces
- tools overlapping each other in one batch

Fix:

- enlarge or offset the tool so faces cross instead of touch (extend ~1 mm
  beyond both faces for through-cuts)
- pass boolean tools in ONE list operand, not pairwise accumulation
- split tool families into staged subtracts when a batch returns slivers

### Fillet or chamfer failure

Likely causes:

- radius/length exceeds local geometry
- selected edges include tiny or unintended edges
- boolean operation created complex edge topology

Fix:

- reduce radius/length (retry at half radius once, then degrade — see the
  build123d-modeling reference `safe_fillet` pattern)
- filter selected edges more narrowly
- apply fillets later in the model
- split edge groups by feature intent
- do not 3D-chamfer tangent chains or multi-arc outlines; bake the bevel into
  the section profile instead

### Invalid or missing geometry

Likely causes:

- open sketch
- subtractive profile outside target
- zero thickness
- inverted solid (positive volume check)

Fix:

- close profiles intended to become faces
- verify dimensions are positive
- make subtractive tools pass through when through-cuts are intended
- simplify the failing feature and rebuild incrementally

### Wrong scale or bounding box

Likely causes:

- units mismatch (declarative schema is millimetres only)
- mistaken diameter/radius
- extrusion direction or amount wrong
- centered vs MIN-aligned origin confusion

Fix:

- check parameter values
- measure critical extents against the reported bounding box
- correct source dimensions; never rotate the preview to hide an axis problem

### Assembly interference failure

Likely causes:

- separate solids volumetrically overlapping (ribs/bosses placed as siblings
  instead of fused into the parent)
- pins/bolts modeled without clearance inside their bores
- forgotten clearance between moving parts

Fix:

- fuse ribs/gussets into their parent component so the pair disappears
- model hole radius = shaft radius + clearance from named parameters
- if the overlap is a genuine intended contact (pin through bore, press-fit),
  declare the pair via allowed_contact in the design; every undeclared pair
  still fails the check

### Selector fragility

Likely causes:

- arbitrary index selection
- topology changed after fillet or boolean
- similar faces/edges are indistinguishable

Fix:

- select by axis, plane, position, normal, or bounding position
- re-select after every boolean or fillet
- add construction datums or simplify operations if needed

### Wrong placement

Fix the local coordinate frame rather than adding a compensating transform at
the end. Build at the origin, transform, then place.

## Reporting failed repairs

If a check cannot be repaired within the turn's attempt budget, report:

```text
- what failed
- what was tried
- which artifact is still usable (STEP, report.json, preview.png are preserved)
- which validation claims cannot be made
- what the next source-level correction should be
```

Never claim success from a Python exit code. The CAD tools reopen the exported
STEP, validate BREP topology, and render a snapshot; report their actual
result.