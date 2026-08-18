#!/usr/bin/env python3
"""Fail if requirements.txt and [project.dependencies] declare different packages.

pyproject.toml is the source of truth — the installers, `pip install -e .` and
uv.lock all resolve from it — so requirements.txt is maintained by hand and
nothing enforces that the two agree. It has drifted twice. The second time it
listed three packages while the engine needed five, and because `mcp` and `ddgs`
are both imported lazily the result was not an ImportError but a quietly reduced
agent: MCP servers failed to connect, and web_search reported its keyless
fallback unavailable, so a stopped SearXNG left it with nothing to fall back to.

Compares package NAMES, not version specifiers: the two files legitimately differ
in how tightly they pin, and a check that failed on that would be turned off.

Usage: python scripts/check_requirements_sync.py [pyproject.toml] [requirements.txt]
Exits 1 and names the difference if they disagree; exits 0 otherwise.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# PEP 508: a name, then optional extras/version/marker. Anything after one of
# these characters is a constraint rather than part of the package name.
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalise(name: str) -> str:
    """PEP 503 normalisation: InquirerPy, inquirerpy and discord-py all compare
    equal to their canonical form, so a casing difference is not a false alarm."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _names(requirement_lines) -> set:
    found = set()
    for line in requirement_lines:
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):   # skip -r/-e/--flag lines
            continue
        match = _NAME_RE.match(line)
        if match:
            found.add(_normalise(match.group(1)))
    return found


def pyproject_dependencies(path: Path) -> set:
    text = path.read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
    if not block:
        raise SystemExit(f"{path}: no [project.dependencies] block found")
    # Comments are stripped before the quoted strings are collected. The block is
    # heavily commented, and a comment that quotes a phrase would otherwise be
    # read as a requirement: a line ending `... degraded to "no backend"` made
    # this script report a missing package called `no`.
    lines = [line.split("#", 1)[0] for line in block.group(1).splitlines()]
    return _names(re.findall(r'"([^"]+)"', "\n".join(lines)))


def requirements_dependencies(path: Path) -> set:
    return _names(path.read_text(encoding="utf-8").splitlines())


def main(argv: list) -> int:
    pyproject = Path(argv[0]) if argv else ROOT / "pyproject.toml"
    requirements = Path(argv[1]) if len(argv) > 1 else ROOT / "requirements.txt"

    declared = pyproject_dependencies(pyproject)
    listed = requirements_dependencies(requirements)
    if declared == listed:
        print(f"requirements.txt matches [project.dependencies] "
              f"({len(declared)} package(s)).")
        return 0

    print("requirements.txt and pyproject.toml disagree:")
    for name in sorted(declared - listed):
        print(f"  missing from requirements.txt: {name}")
    for name in sorted(listed - declared):
        print(f"  not a declared dependency:     {name}")
    print("\nrequirements.txt mirrors [project.dependencies]; update it to match.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
