"""Cheap checks of a file the moment it is written: a parse that runs nothing.

A broken file found at the end of a run costs minutes per fix; found on the next turn, it costs one edit. After
write_file or edit_file lands a file, its kind decides the check:

- Python and JSON are parsed.
- JavaScript goes through `node --check`, which parses the file and runs none of it.
- An HTML page's local src and href files must exist next to it (links to other sites, anchors, data: and mailto:
  are not files).

Each check is read only and takes well under a second; a file over MAX_BYTES is skipped. The result is one line, or
"" when nothing is wrong or the file is not a kind checked here.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

MAX_BYTES = 1_000_000
NODE_TIMEOUT = 5
_JS = (".js", ".mjs", ".cjs")
_PAGE = (".html", ".htm")
_REF_RE = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"'#][^"']*)["']""", re.IGNORECASE)
_NOT_A_FILE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|/|\{|\$)", re.IGNORECASE)   # a scheme, protocol-relative, root, template


def check_file(path: Path, node: str | None) -> str:
    """One line describing what is wrong with the file, or ""."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in (".py", ".json", *_JS, *_PAGE):
        return ""
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""
    if suffix == ".py":
        try:
            ast.parse(text, filename=path.name)
        except SyntaxError as exc:
            return f"line {exc.lineno}: {exc.msg}"
        return ""
    if suffix == ".json":
        if not text.strip():
            return "the file is empty"
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"line {exc.lineno}: {exc.msg}"
        return ""
    if suffix in _JS:
        return _node_check(path, node)
    return _missing_page_files(path, text)


def _node_check(path: Path, node: str | None) -> str:
    if not node:
        return ""
    try:
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=NODE_TIMEOUT,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode == 0:
        return ""
    lines = [line.strip() for line in (result.stderr or result.stdout).splitlines() if line.strip()]
    where = next((line for line in lines if re.search(r":\d+$", line)), "")
    error = next((line for line in lines if "Error" in line), lines[-1] if lines else "it does not parse")
    line_no = re.search(r":(\d+)$", where)
    return (f"line {line_no.group(1)}: " if line_no else "") + error[:160]


def _missing_page_files(path: Path, text: str) -> str:
    missing = []
    for ref in _REF_RE.findall(text):
        ref = ref.strip().split("?", 1)[0].split("#", 1)[0]
        if not ref or _NOT_A_FILE.match(ref) or ref in missing:
            continue
        if not (path.parent / ref).exists():
            missing.append(ref)
    if not missing:
        return ""
    shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
    return f"points at local files that are not there: {shown}"
