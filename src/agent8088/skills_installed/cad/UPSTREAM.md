# Upstream CAD skill

- Source: https://github.com/earthtojake/text-to-cad, `skills/cad/`
- Pinned commit: `0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6` (same commit already verified
  for the vendored `cad-viewer` skill)
- License: MIT (`LICENSE` in this directory, unmodified)

This directory is a verbatim, unmodified copy of upstream's `skills/cad/` at the pinned
commit -- SKILL.md, references/, scripts/ (including the vendored `scripts/packages/`
subtree providing `inspect_refs`, a source copy of `cadgen`, and `cadjs`), agents/,
requirements.txt, and LICENSE. Agent8088 does not maintain a separate hand-ported version
of this skill; the model drives it via the generic `execute_shell`/`write_file` tools,
exactly as it would in any other coding agent that installs this skill (Claude Code, Codex,
Grok Build). Only `open_cad_viewer` (Agent8088's own tool, backed by the separately
vendored `cad-viewer` skill) is not part of this tree.

`scripts/inspect/__main__.py` prefers its own vendored `scripts/packages/cadgen` over the
CAD runtime venv's pip-installed `cadgen==0.4.28` when both are present. This vendored copy
is untouched from upstream and is not asserted to match the pinned pip version exactly --
`tests/test_cad_skill_scripts.py` covers the actual invocation path used at runtime.

To update: re-run the vendor step against a newer commit, replace this directory wholesale
(do not hand-edit inside it), and update the pinned commit above.
