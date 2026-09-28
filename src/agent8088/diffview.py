"""Unified-diff parsing shared by the CLI renderer and the web server.

Kept free of Rich so the web server can import it. The CLI's syntax
highlighting (`_styled_rows`) stays in cli.py, where Rich already lives.
"""

from __future__ import annotations

import re
from typing import Iterable

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

DEFAULT_TAB_WIDTH = 4


def parse_hunks(diff_lines: Iterable[str], tab_width: int = DEFAULT_TAB_WIDTH) -> list[dict]:
    """A unified diff regrouped into hunks of (marker, old_no, new_no, code) rows.

    Two jobs beyond grouping. It tracks each side's line number so a gutter can
    show where in the file the change actually landed. And it strips the trailing
    newline difflib leaves on every body line (`keepends=True`) -- appending
    another one is what made every diff the CLI ever printed come out
    double-spaced, at half the content per screen.
    """
    hunks: list[dict] = []
    old_no = new_no = 0
    for raw in diff_lines:
        line = str(raw).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        header = HUNK_RE.match(line)
        if header:
            old_no, new_no = int(header.group(1)), int(header.group(3))
            hunks.append({
                "old_count": int(header.group(2)) if header.group(2) else 1,
                "rows": [],
            })
            continue
        # Everything before the first hunk is preamble. Recognising '--- '/'+++ '
        # by shape anywhere would swallow real content: deleting a line that opens
        # with '-- ' -- an SQL or Haskell comment -- produces exactly '--- ...'.
        if not hunks:
            continue
        prefixed = line[:1] in ("+", "-", " ")
        marker = line[0] if prefixed else " "
        code = (line[1:] if prefixed else line).expandtabs(tab_width)
        rows = hunks[-1]["rows"]
        if marker == "+":
            rows.append(("+", None, new_no, code))
            new_no += 1
        elif marker == "-":
            rows.append(("-", old_no, None, code))
            old_no += 1
        else:
            rows.append((" ", old_no, new_no, code))
            old_no += 1
            new_no += 1
    return hunks


def diff_path(diff_lines: Iterable[str]) -> str:
    """The file a diff is against, taken from its own '+++' header.

    Only the preamble is searched, for the same reason parse_hunks stops looking
    there: an added line reading '++ tally' arrives as '+++ tally'.
    """
    for line in diff_lines:
        text = str(line)
        if HUNK_RE.match(text):
            break
        if text.startswith("+++ "):
            return text[4:].strip()
    return ""


def diff_counts(diff_lines: Iterable[str]) -> tuple[int, int]:
    """(added, removed) -- the shape of a change, readable before the change itself."""
    added = removed = 0
    in_hunk = False
    for raw in diff_lines:
        line = str(raw)
        if HUNK_RE.match(line):
            in_hunk = True
        elif not in_hunk:
            continue  # preamble: '--- old' / '+++ new' are not changed lines
        elif line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


_TONE = {"+": "add", "-": "del", " ": "ctx"}


def to_payload(diff_lines, *, max_lines: int = 400) -> dict | None:
    """A JSON-serializable diff for the web UI, or None when nothing changed.

    Capped because a diff crosses a WebSocket frame: a 10k-line rewrite would
    push megabytes at a component that renders a hover card.
    """
    diff_lines = list(diff_lines)
    hunks = parse_hunks(diff_lines)
    if not hunks:
        return None
    added, removed = diff_counts(diff_lines)
    lines: list[dict] = []
    truncated = False
    for hunk in hunks:
        for marker, _old_no, _new_no, code in hunk["rows"]:
            if len(lines) >= max_lines:
                truncated = True
                break
            lines.append({"text": code, "tone": _TONE.get(marker, "ctx")})
        if truncated:
            break
    return {
        "file": diff_path(diff_lines),
        "added": added,
        "removed": removed,
        "lines": lines,
        "truncated": truncated,
    }
