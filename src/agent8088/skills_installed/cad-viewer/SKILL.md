---
name: cad-viewer
description: Open and visually review STEP, STP, STL, 3MF, GLB, and DXF artifacts in Agent8088's managed local text-to-cad CAD Viewer.
---

# CAD Viewer

Use `open_cad_viewer` whenever the user asks to open, inspect, measure, explode,
clip, or visually review a CAD artifact. After creating or changing CAD, hand
the canonical STEP artifact to this tool unless the user explicitly declines
interactive review.

## Runtime contract

- Pass an explicit existing file path or the bare artifact filename returned by
  a CAD generation tool. Never use a shell to start a server or browser.
- The managed Viewer binds only to `127.0.0.1`. It must never bind to a LAN/public
  interface and must not be proxied externally; loopback is its trust boundary.
- Agent8088 reuses a healthy Viewer on ports 3245-3255 or starts one from the
  checksum-pinned text-to-cad runtime. It returns the exact review URL.
- One Viewer can browse the artifact's directory. Do not start one process per
  model.
- If Viewer startup fails, report the actionable failure and retain the STEP,
  report, and deterministic PNG preview. Viewer failure must not invalidate CAD
  geometry that already passed deterministic validation.

## Review workflow

1. Generate or identify the canonical STEP/STP artifact.
2. Confirm deterministic validation succeeded: solids, bounding box, topology,
   and assembly interference.
3. Call `open_cad_viewer` with the artifact.
4. Ask the user to use the assembly tree and visual controls when subjective
   design review is needed; do not claim that visual review proves manufacturability.
5. When a user pastes a selected CAD reference, resolve it against the named
   artifact before modifying geometry.

## Viewer capabilities

- STEP/STP: labeled assembly tree, part hide/show and focus, topology selection,
  solid/rendered/x-ray/hidden/line/wire display modes, clip planes, and exploded
  layouts.
- STL/3MF/GLB: orbit/pan/zoom, screenshots, theme/display controls, and
  vertex-to-vertex mesh measurement.
- DXF: read-only flat-pattern viewing when a valid DXF artifact is supplied.
- General: file catalog, orthographic view sphere, annotations, screenshots,
  floor/grid/theme controls, and local download/reveal actions.

Measurements on triangulated mesh formats snap to mesh vertices and are less
authoritative than STEP topology. Keep STEP as the canonical engineering output.

## Security and reliability

- Never construct Viewer URLs yourself; use the tool's encoded URL.
- Never expose a directory broader than the artifact's authorized workspace.
- Do not treat a running process or HTTP 200 alone as success. Agent8088 checks
  the Viewer identity endpoint and catalog before returning a URL.
- The installed browser bundle and backend come from a fixed text-to-cad commit,
  verified by SHA-256. Runtime CAD packages remain version-pinned and isolated.

## Provenance

Adapted from earthtojake/text-to-cad's `cad-viewer` skill and Viewer runtime
(MIT). Geometry continues to be authored with gumyr/build123d (Apache-2.0).
