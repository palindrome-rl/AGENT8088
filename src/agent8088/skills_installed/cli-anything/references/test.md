# Test a CLI-Anything Harness

Write `TEST.md` before implementing missing tests. Keep two layers:

- `test_core.py`: deterministic units for state, validation, serialization,
  backend argument construction, undo/redo, and JSON schemas.
- `test_full_e2e.py`: installed-console subprocess workflows against the real
  backend wherever it is available.

Required checks:

1. `pip install -e .` succeeds in an isolated environment.
2. `cli-anything-<software> --help` succeeds.
3. No-argument invocation reaches the REPL without crashing.
4. A representative `--json` command returns parseable JSON.
5. Invalid inputs fail predictably with non-zero status and useful errors.
6. A multi-step workflow produces a real artifact and reopens or inspects it.
7. Existing commands still pass after refinement.

Never count mocks as proof that the real application integration works. When a
backend cannot run in the current environment, mark that test skipped with the
exact missing prerequisite and do not report it as verified.
