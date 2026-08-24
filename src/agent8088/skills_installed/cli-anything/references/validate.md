# Validate a CLI-Anything Harness

Confirm all of the following:

- package namespace is `cli_anything.<software>`;
- `setup.py` exposes `cli-anything-<software>` through `console_scripts`;
- no top-level `cli_anything/__init__.py` breaks namespace coexistence;
- no-argument invocation enters the REPL;
- one-shot commands work independently;
- `--json` is parseable and stable;
- state is persisted only where documented;
- undo/redo is real where advertised;
- backend code invokes the actual application/API rather than a fake clone;
- filesystem paths are validated and shell interpolation is avoided;
- unit and installed-console end-to-end tests exist and pass;
- README and TEST.md match observed behaviour;
- one representative artifact is produced and inspected.

Return a pass/fail checklist with commands run, evidence, skipped checks, and
specific remediation. A source review without execution is not a full pass.
