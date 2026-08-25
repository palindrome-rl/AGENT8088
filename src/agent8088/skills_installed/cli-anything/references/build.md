# Build a CLI-Anything Harness

Use this workflow only after catalog search shows no suitable existing harness.

## 1. Acquire and analyze

- Use an existing local source path or clone the user-supplied repository.
- Identify the real executable, scripting API, data model, project format,
  existing CLI surfaces, headless modes, and export/render mechanisms.
- Map important GUI operations to backend operations. Prefer the real backend;
  do not reimplement application behaviour merely to make a demo pass.
- Record uncertain or unavailable backend capabilities before designing.

## 2. Design

Create the harness under `<target>/agent-harness/` using the namespace
`cli_anything.<software>`. Design composable Click command groups, stable exit
codes, and a state/project model. Include:

- one-shot subcommands;
- default REPL mode when no subcommand is supplied;
- global `--json` machine-readable output;
- session persistence and undo/redo where the backend supports it;
- preview or export commands when visual verification matters.

The console entry point must be `cli-anything-<software>`.

## 3. Implement

Recommended structure:

```text
agent-harness/
├── setup.py
├── TEST.md
└── cli_anything/
    └── <software>/
        ├── __init__.py
        ├── __main__.py
        ├── <software>_cli.py
        ├── core/
        ├── utils/
        └── tests/
```

Use `find_namespace_packages(include=["cli_anything.*"])`; do not add a
top-level `cli_anything/__init__.py`. Put real executable/API adaptation in a
clearly named backend module. Avoid shell interpolation and validate all paths.

## 4. Plan tests

Create `TEST.md` before writing tests. Cover command parsing, state transitions,
undo/redo where supported, malformed input, missing backend software, JSON
schemas, and at least one complete real-backend workflow.

## 5. Write and run tests

Load `references/test.md`, install the package in an isolated environment, run
the installed console entry point through subprocess, and execute a complete
workflow that creates or modifies a real artifact. Validate `--json` output by
parsing it.

## 6. Document results and agent usage

Record actual test commands and outcomes in `TEST.md`. Add the canonical
`skills/cli-anything-<software>/SKILL.md` plus a packaged compatibility copy at
`cli_anything/<software>/skills/SKILL.md`. Document backend prerequisites,
one-shot examples, JSON examples, previews, and known limitations.

## 7. Package, install, and validate the console command

Create package metadata, install into an isolated environment, confirm
`cli-anything-<software> --help`, and run the complete artifact-producing
workflow through the installed entry point. Publishing outside the workspace
requires separate user authorization. Use an auditor sub-agent for a final
independent check when sub-agent execution is available.

The upstream methodology source is HKUDS CLI-Anything's
`cli-anything-plugin/HARNESS.md`; the pinned revision is recorded in
`UPSTREAM.json`.
