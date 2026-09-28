"""Shaping tool output for the model: denoise, clamp tail-first, read the verdict.

The bug this module exists to prevent: a test run's summary sits on the last
line of the output, the old cap kept `result[:3000]`, and the model was handed a
column of PASSED lines with the verdict cut off. Nothing said the suite failed,
so "did it pass?" got answered from the shape of the visible text.

Three ideas, in the order they apply:

1. `denoise` removes bytes that carry no meaning — colour codes, progress-bar
   redraws, a warning repeated four hundred times. Often this alone brings the
   output under budget, so no truncation happens at all.
2. `clamp` keeps both ends when it must cut, giving most of the budget to the
   tail, because that is where runners put their result. Top-down reads (a
   source file) opt into `keep="head"` and behave as they always did.
3. `detect_verdict` reads the result rather than inferring it, and answers
   "unknown" when it cannot. Head-and-tail is a good guess about where the
   answer lives; it is still a guess, and a guess is what caused the bug.

`denoise` is deliberately not applied to file reads: collapsing repeated lines
in a source file would corrupt text the model then edits from.
"""
from __future__ import annotations

import re
import json
import uuid
from dataclasses import dataclass, field

# CSI sequences (colour, cursor moves) plus the OSC title-setting form. Written
# out rather than using a "strip everything after ESC" shortcut, which eats real
# text when a tool prints a bare 0x1b.
_ANSI_CSI = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_OTHER = re.compile(r"\x1b[@-Z\\-_]")

# Collapsing two identical lines into one line plus a marker makes the output
# longer, so the floor is three.
_REPEAT_FLOOR = 3

ELISION = "[tool result truncated: {dropped} characters elided — {hint}]"
DEFAULT_HINT = "ask for the middle with read_content"


def strip_ansi(text: str) -> str:
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OSC.sub("", text)
    return _ANSI_OTHER.sub("", text)


def denoise(text: str) -> str:
    """Remove bytes that carry no information, without touching what does.

    Idempotent: running it on its own output changes nothing, which matters
    because a cached result can pass through here more than once.
    """
    if not text:
        return text

    text = strip_ansi(text.replace("\r\n", "\n"))

    lines = []
    for raw in text.split("\n"):
        # A progress bar rewrites one line with \r; only its final state is
        # information. Everything before the last \r has been overwritten on a
        # real terminal and should not reach the model as separate lines.
        if "\r" in raw:
            raw = raw.rsplit("\r", 1)[-1]
        # Trailing spaces are kept: fixed-width records, expected-output files
        # and `cat -A` checks depend on them. Only a whitespace-only line counts
        # as blank below.
        lines.append(raw)

    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        run = 1
        if not line.strip():
            # Blank lines group with each other even when their (invisible)
            # whitespace differs, now that trailing spaces are kept.
            while index + run < len(lines) and not lines[index + run].strip():
                run += 1
        while index + run < len(lines) and lines[index + run] == line:
            run += 1

        if not line.strip():
            # Any run of blank or whitespace-only lines becomes one separator.
            # Trailing spaces are real data (fixed-width records), so a line is
            # judged blank by strip(), never rewritten to be blank.
            out.append("")
            index += run
            continue

        out.append(line)
        if run >= _REPEAT_FLOOR:
            out.append(f"[previous line repeated {run} times]")
        else:
            out.extend([line] * (run - 1))
        index += run

    return "\n".join(out).strip("\n")


def clamp(text: str, limit: int, keep: str = "tail", head_share: float = 0.25,
          hint: str = DEFAULT_HINT) -> str:
    """Cut `text` to `limit` characters, keeping the end unless told otherwise.

    `keep="tail"` splits the budget head/tail so the model sees both what ran and
    how it came out. `keep="head"` is for output read top-down, where the front
    is the answer and the tail is filler.
    """
    if limit <= 0 or len(text) <= limit:
        return text

    if keep == "head":
        dropped = len(text) - limit
        return text[:limit] + "\n" + ELISION.format(dropped=dropped, hint=hint)

    head_len = max(1, int(limit * head_share))
    tail_len = limit - head_len
    head, tail = text[:head_len], text[-tail_len:]
    dropped = len(text) - head_len - tail_len
    marker = ELISION.format(dropped=dropped, hint=hint)
    return f"{head}\n{marker}\n{tail}"


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

PASSED, FAILED, UNKNOWN = "passed", "failed", "unknown"


@dataclass(frozen=True)
class Diagnostic:
    """One source location reported by a test runner or compiler."""
    path: str
    line: int
    message: str
    column: int | None = None
    severity: str = "error"

    def as_dict(self) -> dict:
        item = {"path": self.path, "line": self.line, "severity": self.severity,
                "message": self.message}
        if self.column is not None:
            item["column"] = self.column
        return item


@dataclass(frozen=True)
class Verdict:
    """What a command actually reported. `unknown` is a real answer, not a gap."""
    status: str = UNKNOWN
    summary: str = ""
    source: str = ""
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def known(self) -> bool:
        return self.status in (PASSED, FAILED)

    def banner(self) -> str:
        if not self.known:
            return ""
        banner = f"[verdict: {self.status.upper()}] {self.summary}".rstrip()
        if self.diagnostics:
            banner += "\n" + json.dumps(
                {"diagnostics": [item.as_dict() for item in self.diagnostics]},
                ensure_ascii=False, separators=(",", ":"),
            )
        return banner


_EXIT_STATUS = re.compile(r"Command exited with status (\d+)")
# "no tests ran" / "collected 0 items" is neither a pass nor a failure, and
# reading it as a pass is exactly the mistake this module is here to stop.
_NO_TESTS = re.compile(r"no tests ran|collected 0 items|Ran 0 tests", re.I)
_DIAGNOSTIC = re.compile(
    r"^\s*(?P<path>.+?):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?:(?P<severity>fatal error|error|warning|note)\s*:\s*)?(?P<message>.+?)\s*$",
    re.I,
)
_TRACEBACK_FRAME = re.compile(r'^\s*File "(?P<path>.+)", line (?P<line>\d+)')
_EXCEPTION = re.compile(r"^(?P<name>[A-Za-z_][\w.]*(?:Error|Exception|Failure))(?::\s*(?P<message>.*))?$")
_MAX_DIAGNOSTICS = 20


def parse_diagnostics(text: str) -> tuple[Diagnostic, ...]:
    """Extract common source diagnostics without guessing at unfamiliar output."""
    found: list[Diagnostic] = []
    traceback_frame: tuple[str, int] | None = None

    def add(path: str, line: int, message: str, column: int | None = None,
            severity: str = "error") -> None:
        item = Diagnostic(path.strip(), line, message.strip(), column, severity.lower())
        if item.path and item.message and item not in found and len(found) < _MAX_DIAGNOSTICS:
            found.append(item)

    for raw in text.splitlines():
        line = raw.strip()
        frame = _TRACEBACK_FRAME.match(raw)
        if frame:
            traceback_frame = (frame.group("path"), int(frame.group("line")))
            continue
        match = _DIAGNOSTIC.match(raw)
        if match:
            severity = (match.group("severity") or "error").lower()
            add(match.group("path"), int(match.group("line")), match.group("message"),
                int(match.group("column")) if match.group("column") else None, severity)
            continue
        exception = _EXCEPTION.match(line)
        if exception and traceback_frame:
            add(traceback_frame[0], traceback_frame[1],
                exception.group("message") or exception.group("name"))
            traceback_frame = None
    return tuple(found)


def _counted(pattern: str, line: str):
    match = re.search(pattern, line, re.I)
    return int(match.group(1)) if match else None


def _scan_line(line: str) -> str | None:
    """Read one line as a runner summary, or return None if it isn't one.

    Failure checks come first so a line reporting both counts ("3 passed;
    1 failed") lands on failed. Counts are compared against zero rather than
    merely matched, because "0 failed" appears in passing cargo output.
    """
    failed = _counted(r"\b(\d+)\s+(?:tests?\s+)?failed\b", line)
    if failed:
        return FAILED
    if re.search(r"\bFAILED\s*\(", line) or re.search(r"test result:\s*FAILED", line, re.I):
        return FAILED
    if re.match(r"\s*(?:---\s*)?FAIL\b", line):
        return FAILED
    errors = _counted(r"\b(\d+)\s+errors?\b", line)
    if errors:
        return FAILED

    passed = _counted(r"\b(\d+)\s+(?:tests?\s+)?passed\b", line)
    if passed:
        return PASSED
    if re.search(r"test result:\s*ok\b", line, re.I):
        return PASSED
    return None


def detect_verdict(text: str) -> Verdict:
    """Report what the output says, or `unknown`. Never infer from vibes.

    A bare "PASSED" line is one test's result, not the suite's, so a summary
    must carry a count. Anything unrecognised stays unknown: an honest gap the
    model can act on beats a confident wrong answer.
    """
    if not text:
        return Verdict()

    diagnostics = parse_diagnostics(text)
    exit_match = _EXIT_STATUS.search(text)
    exit_failed = bool(exit_match) and int(exit_match.group(1)) != 0

    if _NO_TESTS.search(text):
        # A nonzero exit still means the command failed, even with no tests.
        if exit_failed:
            return Verdict(FAILED, exit_match.group(0), source="exit status", diagnostics=diagnostics)
        return Verdict(UNKNOWN, source="no tests ran")

    # unittest prints a bare "OK" with no counts. On its own that is far too
    # weak a signal, so it only counts as a verdict under a "Ran N tests" line.
    ran_tests = bool(re.search(r"\bRan \d+ tests?\b", text))

    for line in reversed(text.split("\n")):
        line = line.strip()
        if not line:
            continue
        status = _scan_line(line)
        if status is None:
            if ran_tests and re.fullmatch(r"OK(?:\s*\(.*\))?", line):
                status = PASSED
            else:
                continue
        # A summary claiming success under a nonzero exit is not success: a
        # later command in the same shell failed, and saying "passed" here
        # would hide it.
        if status == PASSED and exit_failed:
            return Verdict(FAILED, f"{line} (but {exit_match.group(0)})",
                           source="summary + exit status", diagnostics=diagnostics)
        return Verdict(status, line, source="summary line",
                       diagnostics=diagnostics if status == FAILED else ())

    if exit_failed:
        return Verdict(FAILED, exit_match.group(0), source="exit status", diagnostics=diagnostics)
    return Verdict()


# ---------------------------------------------------------------------------
# Keeping the whole thing
# ---------------------------------------------------------------------------

@dataclass
class OutputStore:
    """Full tool outputs, addressable so the elided middle stays retrievable.

    Bounded because a long session would otherwise hold every byte a command
    ever printed; the oldest entries drop out first.
    """
    max_entries: int = 40
    _texts: dict = field(default_factory=dict)
    _order: list = field(default_factory=list)

    def put(self, tool: str, text: str) -> str:
        ref = f"{tool}-{uuid.uuid4().hex[:8]}"
        self._texts[ref] = text
        self._order.append(ref)
        while len(self._order) > self.max_entries:
            self._texts.pop(self._order.pop(0), None)
        return ref

    def refs(self) -> list:
        return list(self._order)

    def get(self, ref: str):
        return self._texts.get(ref)

    def window(self, ref: str, offset: int = 0, length: int = 2_000):
        text = self._texts.get(ref)
        if text is None:
            return None
        offset = max(0, offset)
        chunk = text[offset:offset + max(1, length)]
        end = offset + len(chunk)
        return {
            "text": chunk,
            "total": len(text),
            "offset": offset,
            "next_offset": end if end < len(text) else None,
        }
