#!/usr/bin/env python3
"""Fail if any module defines the same top-level function/class name twice.

Python silently keeps only the last definition when a name is redefined —
no SyntaxError, no ImportError. This has bitten this codebase before: two
functions both named `_wrap_untrusted` coexisted in engine.py (one general,
one MCP-specific) after two feature branches were developed against the
same file, and the linter in use at the time (ruff/pyflakes F811) did not
flag it in practice on that file. This script checks with certainty
instead of relying on a heuristic.

Usage: python scripts/check_duplicate_defs.py [path ...]
Exits 1 and prints one line per collision if any are found; exits 0 otherwise.
"""
import ast
import sys
from pathlib import Path

DEFAULT_ROOTS = ["src/agent8088"]


def find_duplicates(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        return [f"{path}: SyntaxError while parsing: {e}"]

    seen: dict[str, int] = {}
    problems = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            if name in seen:
                problems.append(
                    f"{path}:{node.lineno}: '{name}' redefines the one at "
                    f"line {seen[name]} (only the later definition is ever used)"
                )
            seen[name] = node.lineno
    return problems


def main(argv: list[str]) -> int:
    roots = [Path(p) for p in (argv or DEFAULT_ROOTS)]
    files = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.py")))

    problems = []
    for f in files:
        problems.extend(find_duplicates(f))

    if problems:
        print("Duplicate top-level definitions found:")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"No duplicate top-level definitions in {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
