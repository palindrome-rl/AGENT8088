# Upstream CAD components

- `build123d` 0.11.1 — https://github.com/gumyr/build123d — Apache-2.0.
- `cadgen` 0.4.28 and the adapted CAD workflow/snapshot/Viewer assets from
  `earthtojake/text-to-cad` — https://github.com/earthtojake/text-to-cad — MIT.

Agent8088 pins these versions in an isolated runtime. The text-to-cad snapshot
assets are redistributed with their MIT license in
`agent8088/cad_snapshot_runtime/TEXT_TO_CAD_LICENSE.txt`; the Viewer installer
also verifies a pinned upstream commit/archive hash and installs its LICENSE.
