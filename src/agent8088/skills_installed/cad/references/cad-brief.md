# CAD brief (Agent8088 port)

Read this file when converting a user's request — prose, reference images,
technical drawings, or a combination — into a CAD brief before calling
`generate_cad_design` or `generate_cad_model`. Ported from
earthtojake/text-to-cad's CAD skill (MIT). The brief is an internal
note-taking scaffold; do not ask the user to fill it out, and do not require
the user to provide JSON. If the user supplied JSON voluntarily, extract the
same information but continue the workflow in prose notes and build123d
source.

## Goal

Convert the request into an actionable modeling brief before writing source or
calling tools. Every input modality funnels into the same brief.

The brief should answer:

- What is being modeled, and is it a part, assembly, modification, inspection
  task, or secondary output request?
- What dimensions and units are specified, and which missing dimensions are
  inferable?
- Which features are required?
- Which faces, axes, origins, joints, or interfaces control positioning?
- What output files are requested (Agent8088 writes every artifact into its
  artifacts directory; pass bare filenames)?
- What must be validated before success is reported?

When inputs conflict, dimensioned sources win over image proportions. When two
dimensioned sources conflict, flag the conflict instead of silently choosing.

## Reference images

An image without stated dimensions is design intent, not a spec:

- Establish scale from one stated dimension or a known object in frame; if
  neither exists and fit matters, that is the one clarification question to
  ask.
- Estimate remaining proportions from the image and record them as assumptions
  like any other inferred value.
- Distinguish reproduction ("model this part") from inspiration ("something
  like this") in the brief.

## Technical drawings

A drawing is a dimensioned contract. Extract it systematically:

- Read the title block and notes first: units, projection convention,
  revision, disclaimers.
- Identify which view is which and which model axes each maps to before
  extracting numbers. Trust callouts and view labels, not layout conventions.
  Section views are the source of truth for internal features.
- Convert every dimension callout into a named parameter and a validation
  target. Multiplicity (`4X`), `TYP.`, and thread/counterbore/countersink
  callouts expand into features plus checks.
- Never scale undimensioned geometry off the image. Derive it from stated
  dimensions when constrained; otherwise assume and report.
- Cross-check features across views; when views disagree, prefer the
  dimensioned view and flag the conflict.

## Brief format

Use concise Markdown notes, not a user-facing structured schema:

```text
CAD brief:
- Model: <part or assembly name>
- Task type: <new part, assembly, modification, inspection, secondary output>
- Units: <explicit or assumed>
- Coordinate convention: <origin, base plane, up axis>
- Overall dimensions: <width/depth/height or equivalent>
- Functional features: <holes, slots, ribs, bosses, pockets, shells, etc.>
- Manufacturing assumptions: <only when relevant>
- Positioning/mating: <interfaces, datums, child placements, joints,
  alignment rules, intended contacts for allowed_contact>
- Paths: <bare STEP filename; secondary outputs if requested>
- Validation targets: <bbox, solid count, labels, spec-driven measurements>
- Assumptions: <only meaningful inferred choices>
```

## Example: mechanism assembly

User says (abridged): "Two-finger gripper: 100×70×8 base, hub Ø40, servo
housing 45×25×42 wall 3, two mirrored fingers 75×15×8 with Ø6 pivots, links
45×10×5, actuator disc Ø32×6, pins with 0.3 clearance."

Agent brief:

```text
CAD brief:
- Model: robotic_gripper assembly, 10 named solids.
- Units: millimeters.
- Origin: base footprint center; +Z up.
- Parameters: base_length=100, base_width=70, base_thickness=8, ...
- Components: Base, ServoHousing, Left/RightPivotSupport, Left/RightFinger,
  Left/RightLink, ActuatorDisc, PivotPins.
- Clearance: pin_clearance=0.3; bore_r = pin_r + pin_clearance everywhere.
- Contacts: pins through support bores and finger pivots — declare
  [[PivotPins, LeftPivotSupport], [PivotPins, RightPivotSupport],
   [PivotPins, LeftFinger], [PivotPins, RightFinger],
   [PivotPins, LeftLink], [PivotPins, RightLink]].
- Ribs fused into their supports (no separate overlapping solid).
- Validation: 10 labeled solids, interference clean outside declared pairs,
  bbox ≈ 100 x (open width) x (base+housing height).
```

## Clarification policy

Ask one focused question only when the missing information affects fit,
safety, compliance, or makes the part impossible to model. Otherwise proceed
with assumptions and report them — never when the user explicitly asked you to
make reasonable engineering assumptions.

Ask when:

- No dimensions are provided for a physical object, and no scale reference
  exists in supplied images.
- A mating interface is described but the mating geometry is unspecified.
- The part is safety-critical, load-bearing, pressure-bearing, medical, or
  compliance-bound.

Do not ask when:

- A default clearance hole standard is sufficient (M3/M4/M5 clearance:
  3.4/4.5/5.5 mm).
- A cosmetic fillet radius can be safely assumed (1–3 mm).
- Origin/orientation can be chosen and reported.

## Success criteria

A brief is ready for modeling when it contains enough information to define:

- units and local coordinate system
- named parameters
- feature plan and component labels
- expected bounding box or key measurements
- validation targets