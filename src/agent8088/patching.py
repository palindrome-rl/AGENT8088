"""Exact search/replace editing for a single text file.

Pure by design: no engine imports, no filesystem access. The engine resolves
paths and enforces guards; this module only decides whether an edit is
unambiguous and what the file becomes.

Fuzziness stops at whitespace on purpose. Looser matching edits the wrong code
silently, and a refusal the model can act on costs one turn, while a wrong edit
the user does not notice costs their trust in every edit after it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EditResult:
    text: str | None = None
    line: int | None = None
    strategy: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.text is not None


def dominant_newline(text: str) -> str:
    """The line ending to write back with. CRLF only when it is the majority."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _occurrences(haystack: str, needle: str) -> list[int]:
    found, start = [], 0
    while True:
        at = haystack.find(needle, start)
        if at < 0:
            return found
        found.append(at)
        start = at + 1  # overlapping matches still count as ambiguity


def _squeeze(text: str) -> str:
    """Trailing whitespace removed per line, and tabs expanded.

    The two ways a model reliably mis-copies code it can see: it adds or drops
    trailing spaces, and it renders a tab as spaces or the reverse.
    """
    return "\n".join(line.expandtabs(4).rstrip() for line in text.split("\n"))


def _closest_line(text: str, needle: str) -> tuple[int, str] | None:
    """The existing line most similar to the first line of `needle`.

    A refusal that only says 'not found' sends the model guessing. Handing back
    the real text of the nearest line is the information it needs to retry
    correctly on the next turn instead of the turn after that.
    """
    import difflib
    target = _squeeze(needle).strip().split("\n")[0]
    if not target:
        return None
    best_ratio, best = 0.0, None
    for number, line in enumerate(text.split("\n"), start=1):
        ratio = difflib.SequenceMatcher(None, target, line.strip()).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, (number, line)
    if best is None or best_ratio < 0.5:
        return None
    return best


def _ambiguous(positions: list[int], body: str) -> str:
    lines = [_line_of(body, at) for at in positions]
    shown = ", ".join(f"line {n}" for n in lines[:2])
    return (f"Error: old_string was found {len(positions)} times ({shown}"
            f"{', and more' if len(lines) > 2 else ''}), so the edit is ambiguous "
            f"and nothing was written. Include more surrounding lines in "
            f"old_string to identify exactly one location.")


def apply_edit(text: str, old: str, new: str) -> EditResult:
    if not old:
        return EditResult(error=(
            "Error: old_string was empty. An empty old_string means replacing the "
            "whole file — use write_file for that. To edit part of a file, pass the "
            "exact existing text you want replaced."))
    if old == new:
        return EditResult(error=(
            "Error: old_string and new_string are identical, so this edit would "
            "change nothing. Nothing was written."))

    newline = dominant_newline(text)
    body, want, repl = _lf(text), _lf(old), _lf(new)

    def restore(edited: str) -> str:
        return edited.replace("\n", newline) if newline == "\r\n" else edited

    positions = _occurrences(body, want)
    if len(positions) == 1:
        at = positions[0]
        edited = body[:at] + repl + body[at + len(want):]
        return EditResult(text=restore(edited), line=_line_of(body, at), strategy="exact")
    if len(positions) > 1:
        return EditResult(error=_ambiguous(positions, body))

    # Nothing exact. Retry against a whitespace-normalized view of the file,
    # mapping the match back to whole real lines so the write stays byte-exact
    # everywhere the model got it right.
    squeezed_body = _squeeze(body)
    squeezed_want = _squeeze(want)
    squeezed_positions = _occurrences(squeezed_body, squeezed_want) if squeezed_want.strip() else []
    if len(squeezed_positions) > 1:
        return EditResult(error=_ambiguous(squeezed_positions, squeezed_body))
    if len(squeezed_positions) == 1:
        at = squeezed_positions[0]
        # The fallback rebuilds by whole lines, so it is only sound when the
        # match covers whole lines. A match starting mid-line would otherwise
        # replace the entire line and silently eat the text either side of it —
        # exactly the class of wrong-edit this module exists to refuse.
        if at == 0 or squeezed_body[at - 1] == "\n":
            start_line = _line_of(squeezed_body, at)
            span = squeezed_want.count("\n") + 1
            lines = body.split("\n")
            edited = "\n".join(lines[:start_line - 1] + repl.split("\n")
                               + lines[start_line - 1 + span:])
            return EditResult(text=restore(edited), line=start_line, strategy="normalized")

    near = _closest_line(body, want)
    hint = ""
    if near is not None:
        number, line = near
        hint = (f" The closest existing line is line {number}: {line.strip()!r}. "
                f"Read the file and copy the text exactly, including indentation.")
    return EditResult(error=(
        f"Error: old_string was not found in the file, so nothing was written.{hint}"))
