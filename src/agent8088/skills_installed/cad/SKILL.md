---
name: cad
description: Write FreeCAD Python for CAD work create_cad_part and convert_cad can't do — booleans, holes, edits to an existing model, assemblies.
version: 1.0.0
category: software-development
---

`create_cad_part` builds one primitive (box/cylinder/sphere/cone/tube) from
a dimension string. `convert_cad` converts an existing file between formats.
Neither writes code. **This skill is for everything past that**: booleans,
holes, multiple shapes in one file, editing a file that already exists.

Check `create_cad_part`/`convert_cad` first — most requests fit one of them
without any code at all. Reach for this skill only when they genuinely can't
do the job.

## Find FreeCAD first, and call it by full path

```
execute_shell: if exist "C:\Users\%USERNAME%\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" (echo FOUND) else (echo MISSING)
```

The official installer puts it at
`%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\freecadcmd.exe` — a per-user
location, not `Program Files`, and not on `PATH`. Also check
`C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe` (a WinGet or portable
install may land there instead). If neither exists, tell the user FreeCAD
isn't installed rather than guessing or claiming success — and never invent
a reason for a failure that has nothing to do with FreeCAD being missing.

**On Windows, `execute_shell` runs `cmd.exe`, not bash.** `dir` not `ls`,
double quotes not single. Same rule as the `documents` skill, repeated here
because it bites every shell-based skill the same way.

## Write the script to a file, run it, then verify on disk

```
write_file: C:\...\script.py
execute_shell: "C:\Users\%USERNAME%\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" C:\...\script.py
```

Then confirm the output file actually exists and re-read it with `read_text`
(which already extracts CAD summaries) before saying anything succeeded.
`freecadcmd` can print a success-looking trace and still not write the file —
disk state is the only thing worth trusting, never the console output.

## Use full Windows paths inside the script, always

**Verified failure**: a Unix-style path (`/tmp/out.step`) inside a FreeCAD
Python script silently fails to export — `freecadcmd` is a native Windows
binary and does not understand it, even though the same path might resolve
in a bash shell. Every path inside the script must be a real Windows path:
`r"C:\Users\...\out.step"` (raw string, or escaped backslashes) — never a
forward-slash Unix-style path, never a bare relative name.

## Reading a format not already in `read_text`

The read side already covers `.fcstd`, `.step`/`.stp`, `.iges`/`.igs`,
`.stl`, `.obj`, `.brep`, `.dxf` — just call `read_text` on those, no script
needed. For anything else, open it the same way a conversion script would
(see below) and print what you need.

## The primitives (verified against a real install)

```python
import FreeCAD, Part
doc = FreeCAD.newDocument("work")

box = Part.makeBox(50, 30, 20)                          # length, width, height
cyl = Part.makeCylinder(10, 50)                          # radius, height
cyl2 = Part.makeCylinder(5, 30,
    FreeCAD.Vector(25, 15, -5), FreeCAD.Vector(0, 0, 1))  # radius, height, base point, direction
sph = Part.makeSphere(25)                                # radius
```

## Booleans — a hole through a plate

```python
import FreeCAD, Part

doc = FreeCAD.newDocument("work")
plate = Part.makeBox(50, 30, 20)
hole = Part.makeCylinder(5, 30, FreeCAD.Vector(25, 15, -5), FreeCAD.Vector(0, 0, 1))
result = plate.cut(hole)   # boolean subtract. .fuse() unions, .common() intersects.

obj = doc.addObject("Part::Feature", "plate")
obj.Shape = result
doc.recompute()
Part.export([obj], r"C:\path\to\output.step")
```

Make the hole's cylinder taller than the plate and offset its base below the
plate's bottom face (as above: base z = -5 for a 20mm-tall box starting at
z=0), so it cuts all the way through — a cylinder exactly the plate's height
can leave a sliver of material at one face from floating-point tolerance.

## Editing a file that already exists — open, add, export all

**Verified**: this is how to add a shape to an existing model without
regenerating what's already there. `Part.insert` loads the existing
geometry into the document as real objects — it does not need touching to
survive; only export the full object list at the end.

```python
import FreeCAD, Part

doc = FreeCAD.newDocument("edit")
Part.insert(r"C:\path\to\existing.step", doc.Name)   # existing shapes are now real objects

new_box = Part.makeBox(10, 10, 10, FreeCAD.Vector(60, 0, 0))
obj = doc.addObject("Part::Feature", "added")
obj.Shape = new_box

doc.recompute()
all_shapes = [o for o in doc.Objects if hasattr(o, "Shape")]
Part.export(all_shapes, r"C:\path\to\existing.step")   # every object, not just the new one
```

**"Add to this file" means the real existing geometry stays in the output.**
Building a fresh box with similar dimensions and calling it done is not the
same task — the original shape is gone. This is the exact failure mode this
agent has hit twice already with documents: asked to modify something real,
it wrote a fresh replacement instead. `Part.insert` then exporting the full
object list is what makes "add to" actually mean add to.

## Assemblies (multiple positioned parts, one file)

There is no dedicated Assembly API verified here — build it as several
`Part::Feature` objects in one document, each given a `Placement` to
position it, and export the full list together:

```python
import FreeCAD, Part

doc = FreeCAD.newDocument("assembly")

base = doc.addObject("Part::Feature", "base")
base.Shape = Part.makeBox(100, 60, 10)

post = doc.addObject("Part::Feature", "post")
post.Shape = Part.makeCylinder(8, 40)
post.Placement = FreeCAD.Placement(FreeCAD.Vector(20, 30, 10), FreeCAD.Rotation())

doc.recompute()
all_shapes = [o for o in doc.Objects if hasattr(o, "Shape")]
Part.export(all_shapes, r"C:\path\to\assembly.step")
```

`FreeCAD.Placement(position_vector, rotation)` moves an object without
mutating its base shape — build each part at the origin, then place it,
rather than baking an offset into the geometry itself.

## What's genuinely unverified past this point

Sketches with constraints, TechDraw drawings (deliberately not part of
`convert_cad` — see its own refusal message), FEM, and real kinematic
assemblies (the `Assembly` workbench proper, not just positioned parts) were
not exercised against a real install when this skill was written. Try the
straightforward FreeCAD API call, verify the output file exists and looks
sane via `read_text`, and say plainly if it didn't work rather than
asserting success on an unread file.
