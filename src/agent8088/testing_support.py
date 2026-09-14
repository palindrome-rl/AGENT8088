"""Test-command discovery and output trimming.

Deliberately dumb and ordered: the language's own runner beats a wrapper, and a
project it cannot recognise gets an error naming every marker it looked for
rather than a guessed command that fails confusingly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Discovery:
    command: str = ""
    framework: str = ""
    error: str | None = None


_MAKE_TEST_RE = re.compile(r"^test\s*:", re.MULTILINE)

# Order is the contract. Checked in this sequence so a Python project that also
# ships a Makefile runs pytest, not `make test`.
_MARKERS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tests",
            "package.json", "go.mod", "Cargo.toml", "Makefile",
            "gradlew", "mvnw")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_pytest(root: Path) -> bool:
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
        return True
    if (root / "tests").is_dir():
        return True
    config = root / "setup.cfg"
    return config.exists() and "[tool:pytest]" in _read(config)


def _npm_has_test_script(root: Path) -> bool:
    manifest = root / "package.json"
    if not manifest.exists():
        return False
    try:
        data = json.loads(_read(manifest))
    except ValueError:
        return False  # a malformed manifest is not evidence of a test script
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return isinstance(scripts, dict) and bool(scripts.get("test"))


MAX_WALK_UP = 8


def discover_command(root: str) -> Discovery:
    """Find the test command for the project containing `root`.

    Walks up from `root` to the nearest directory carrying a marker, because a
    caller naturally names the directory it cares about rather than the project
    root. Live testing caught this: the test-writer sub-agent passed the
    directory the tests live in and was told no test command existed, with
    pyproject.toml sitting one level above it.

    Nearest wins, so a JS sub-project inside a Go repo runs its own suite.
    """
    base = Path(root or ".").expanduser()
    if not base.is_dir():
        return Discovery(error=(
            f"Error: {base} is not a directory, so no test command could be found."))

    start = base
    for _ in range(MAX_WALK_UP):
        found = _discover_here(base)
        if found is not None:
            return found
        parent = base.parent
        if parent == base:  # filesystem root: stop rather than loop
            break
        base = parent

    return Discovery(error=(
        f"Error: no test command could be discovered in {start} or its parent "
        f"directories. Looked for: {', '.join(_MARKERS)}. Run the suite with "
        f"execute_shell if you know the command, or tell the user which one to use."))


def _discover_here(base: Path) -> Discovery | None:
    """The runner for exactly this directory, or None if it carries no marker."""
    if _has_pytest(base):
        return Discovery(command="pytest -q", framework="pytest")
    if _npm_has_test_script(base):
        return Discovery(command="npm test --silent", framework="npm")
    if (base / "go.mod").exists():
        return Discovery(command="go test ./...", framework="go")
    if (base / "Cargo.toml").exists():
        return Discovery(command="cargo test", framework="cargo")
    makefile = base / "Makefile"
    if makefile.exists() and _MAKE_TEST_RE.search(_read(makefile)):
        return Discovery(command="make test", framework="make")
    if (base / "gradlew").exists():
        return Discovery(command="./gradlew test", framework="gradle")
    if (base / "mvnw").exists():
        return Discovery(command="./mvnw -q test", framework="maven")
    return None


def trim_output(text: str, *, max_chars: int = 4000) -> str:
    """Keep the head, which is where a runner puts the failures that matter.

    The full output stays reachable through last_output, so trimming here loses
    nothing -- it only keeps a 20k-line pytest run out of the context window.
    """
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return (f"{text[:max_chars]}\n… output truncated: {dropped} more characters. "
            f"Use last_output with an offset to read the rest.")
