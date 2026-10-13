"""Completion checks: what to verify when the model replies with text and no tool call.

Pure functions and a small budget class. Nothing here calls a model or touches
the engine's globals, so every rule can be tested with a recorded reply.

The engine runs these in order when a reply has no tool call (see
``_run_agent_loop``): an empty reply, a reply that admits unfinished work, files
the request named that do not exist, parts of a multi-part request nobody
addressed, then the verification and test reminders. Every nudge says
"continue", never "give your final answer", and all of them draw on one
continuation budget so a task that cannot be finished still ends.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath

DEFAULT_CONTINUATIONS = 3
# Progress earns more continuations, but never an unbounded number.
TOTAL_CONTINUATION_FACTOR = 4
MAX_SUBGOALS = 8
SUBGOAL_PREFIX = "Part: "

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)


def _prose(text: str) -> str:
    """The text without fenced code, where 'TODO' and 'I will now' mean something else."""
    return _CODE_FENCE.sub(" ", text or "")


# --- 1. A reply that admits the work is unfinished --------------------------------

# Strong admissions only. A reply that merely suggests next steps after finishing
# ("you could add tests next") must not match, which is why bare "next steps" and
# "should" are absent. The patterns are applied only when the run also shows open
# items (see TrajectoryState.has_open_items), so ordinary phrasing in a finished
# answer does not trigger a continuation.
_UNFINISHED = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bnot\s+(?:done|finished|complete|completed|ready|working)\s+yet\b",
    r"\bnot\s+(?:\w+\s+){0,2}?yet\b",
    r"\b(?:isn['’]?t|aren['’]?t|hasn['’]?t|haven['’]?t)\s+(?:\w+\s+){0,2}?yet\b",
    r"\b(?:is|are|was|were|has|have)(?:n['’]?t|\s+not)\s+(?:yet\s+)?(?:been\s+)?"
    r"(?:done|finished|completed?|implemented|written|created|built|started|ready)\b",
    r"\bstill\s+(?:need|needs|have|has)\s+to\b",
    r"\bstill\s+(?:incomplete|unfinished|missing|broken|not\s+(?:done|finished|working|runnable))\b",
    r"\bhave(?:n['’]?t|\s+not)\s+(?:yet\s+)?(?:written|created|built|implemented|started|"
    r"finished|completed|run|tested|added)\b",
    r"\b(?:remaining|outstanding)\s+(?:steps?|work|tasks?|items?|parts?)\b",
    r"\bi(?:['’]ll|\s+will)\s+(?:now|proceed|start|begin|continue|next)\b",
    r"\bnext,\s*i(?:['’]ll|\s+will)\b",
    r"\blet\s+me\s+(?:now|proceed|start|begin|continue)\b",
    r"\bwork\s+in\s+progress\b",
    r"\bto[- ]?do\b",
))


def admits_unfinished_work(reply: str) -> bool:
    """True if the reply says, in plain words, that the work is not finished."""
    text = _prose(reply)
    return any(pattern.search(text) for pattern in _UNFINISHED)


# --- 2. Files the request named ------------------------------------------------------

_EXTENSIONS = (
    "py|pyw|ipynb|js|jsx|mjs|cjs|ts|tsx|html|htm|css|scss|json|jsonl|yaml|yml|toml|ini|cfg|xml|"
    "md|rst|txt|csv|tsv|log|pdf|docx?|xlsx?|pptx?|odt|png|jpe?g|gif|svg|webp|mp[34]|wav|zip|tar|gz|"
    "sh|bash|ps1|bat|c|h|cc|cpp|hpp|java|kt|go|rs|rb|php|swift|cs|sql|lua|r|dockerfile"
)
_PATH_TOKEN = re.compile(
    r"(?<![\w/\\.:@-])((?:[A-Za-z]:[\\/]|~[\\/]|\.{0,2}[\\/])?(?:[\w.-]+[\\/])*[\w-][\w.-]*\.(?:"
    + _EXTENSIONS + r"))(?![\w-])", re.IGNORECASE)
_OUTPUT_CUE = re.compile(
    r"\b(?:write|writes|create|creates|save|saves|generate|generates|make|makes|build|builds|"
    r"produce|produces|output|outputs|export|exports|store|stores|put|implement|implements|draft|"
    r"compose|render|call(?:ed)?|name(?:d)?|as)\b", re.IGNORECASE)
_INPUT_CUE = re.compile(
    r"\b(?:read|reads|open|opens|load|loads|parse|parses|from|using|use|input|given|attached|"
    r"existing|edit|modify|update|fix|refactor|review|analy[sz]e|inspect|look\s+at|based\s+on)\b",
    re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`'\"(])|\n+")


def named_output_paths(request: str) -> list[str]:
    """Paths the request asks to be created, in the order they are named.

    A path counts only when the closest cue before it in its sentence is an
    output verb: "read data.csv and write summary.md" names summary.md, and the
    file it reads is not something the run has to produce.
    """
    found: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(_prose(request)):
        for match in _PATH_TOKEN.finditer(sentence):
            before = sentence[:match.start()]
            output = [m.end() for m in _OUTPUT_CUE.finditer(before)]
            inputs = [m.end() for m in _INPUT_CUE.finditer(before)]
            if not output or (inputs and max(inputs) > max(output)):
                continue
            path = match.group(1).rstrip(".")
            if path not in found:
                found.append(path)
    return found


def _candidates(path: str, roots: Iterable[Path]) -> list[Path]:
    p = Path(os.path.expanduser(path))
    if p.is_absolute():
        return [p]
    return [Path(root) / p for root in roots]


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


_DIR_TOKEN = re.compile(r"(?<![\w/\\.:@-])((?:~?\.{0,2}[\\/])?(?:[\w.-]+[\\/])+)(?=[\s,.;:)`'\"]|$)")
_DIR_WORD = re.compile(
    r"\b(?:folder|directory|dir)\s+(?:called\s+|named\s+)?[`'\"]?([\w.-]+(?:[\\/][\w.-]+)*)",
    re.IGNORECASE)


def named_output_dirs(request: str) -> list[str]:
    """Directories the request asks to be filled (`out/`, "a folder named results")."""
    found: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(_prose(request)):
        spots = [(m.start(), m.group(1).rstrip("/\\")) for m in _DIR_TOKEN.finditer(sentence)]
        spots += [(m.start(), m.group(1)) for m in _DIR_WORD.finditer(sentence)]
        for start, path in spots:
            before = sentence[:start]
            output = [m.end() for m in _OUTPUT_CUE.finditer(before)]
            inputs = [m.end() for m in _INPUT_CUE.finditer(before)]
            if (not output or (inputs and max(inputs) > max(output)) or not path
                    or path in found or path in (".", "..", "~")):
                continue
            found.append(path)
    return found


def _has_content(path: Path) -> bool:
    try:
        return path.is_dir() and any(_nonempty_file(f) for f in path.rglob("*"))
    except OSError:
        return False


def missing_outputs(request: str, reply: str, written: Iterable[str],
                    roots: Iterable[Path], mirror_roots: Iterable[Path] = ()) -> list[str]:
    """Named or claimed deliverables that do not exist, are empty, or sit in the
    wrong place.

    Three sources: paths the request asked for, directories it asked to be
    filled, and paths this run wrote that the reply names as its result (an empty
    or removed file is not one). `mirror_roots` (the artifacts folder) is where
    unspecified work goes by default; a requested path found only there is
    reported as misplaced rather than satisfied. The check is deterministic and
    makes no model call.
    """
    roots = [Path(r) for r in roots]
    mirrors = [Path(r) for r in mirror_roots]
    written = list(written)
    missing: list[str] = []
    for path in named_output_paths(request):
        satisfied = any(_nonempty_file(c) for c in _candidates(path, roots))
        if not satisfied:
            elsewhere = any(_nonempty_file(c) for c in _candidates(path, mirrors))
            missing.append(f"{path} (found only in {mirrors[0]}; create it at the requested path)"
                           if elsewhere and mirrors else path)
    for path in named_output_dirs(request):
        if not any(_has_content(c) for c in _candidates(path, roots)):
            missing.append(f"{path}/ (missing or empty)")
    lowered = (reply or "").lower()
    for path in written:
        name = PurePath(path).name
        if name.lower() in lowered and not _exists_somewhere(path, roots + mirrors) and path not in missing:
            if not any(PurePath(m.replace("\\", "/")).name.startswith(name) for m in missing):
                missing.append(path)
    return missing


def _exists_somewhere(path: str, roots: Iterable[Path]) -> bool:
    """A written path may be recorded as the model spelled it, relative to a workspace."""
    return any(_nonempty_file(c) for c in _candidates(path, roots))


# --- 4. Parts of a multi-part request --------------------------------------------------

_LIST_LINE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(\S.*)$")
_CLAUSE_SPLIT = re.compile(
    r"(?:[,;.]|\band\b)\s*(?:and\s+)?(?:then|also|after\s+that|afterwards|finally)\b[,:]?\s+",
    re.IGNORECASE)
_STOPWORDS = frozenset(
    "the a an and or of to in on for with that this it is are be as at by from into then also "
    "make sure should must can will would your you their there each every all any some please "
    "using use when where which what have has had been being not only just than more most".split())


def extract_subgoals(request: str) -> list[str]:
    """The separate parts of a multi-part request, or [] when it is a single ask.

    Built from a numbered or bulleted list, or from explicit "and also / then"
    clauses; fewer than two items is not a checklist, so a plain request costs
    nothing. A request written as one paragraph with no such structure builds no
    checklist, and its completion relies on the other checks.
    """
    text = _prose(request).strip()
    if not text:
        return []
    items = [m.group(1).strip() for line in text.splitlines()
             if (m := _LIST_LINE.match(line))]
    if len(items) < 2:
        parts = [p.strip(" ,;.") for p in _CLAUSE_SPLIT.split(text)]
        items = [p for p in parts if len(p.split()) >= 3]
        if len(items) < 2:
            return []
    return [i[:160] for i in items[:MAX_SUBGOALS]]


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _keywords(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9_]{4,}", text.lower()) if w not in _STOPWORDS}


def unaddressed_subgoals(items: Iterable[str], reply: str, written: Iterable[str]) -> list[str]:
    """Items whose key words the reply and the written files never mention.

    A part counts as addressed when at least half of its key words appear in the
    reply or in a written file's name. This is deliberately loose: it exists to
    catch a part that was forgotten, and a part done but described in other
    words costs one continuation, not a failure.
    """
    haystack = _keywords(reply or "") | _keywords(" ".join(PurePath(w).name for w in written))
    open_items = []
    for item in items:
        words = _keywords(item)
        if words and len(words & haystack) * 2 < len(words):
            open_items.append(item)
    return open_items


# --- 5. Which changed files deserve a "write tests" reminder ----------------------------

_SCRATCH_DIRS = frozenset({"scratch", "tmp", "temp", "sandbox-tmp", ".scratch", "experiments", "playground"})
_ENTRY_POINTS = frozenset({
    "main.py", "app.py", "run.py", "server.py", "cli.py", "manage.py", "setup.py", "conftest.py",
    "index.js", "main.js", "app.js", "server.js", "index.ts", "main.ts", "main.go", "main.rs"})
_SCRIPT_WORDS = ("scrape", "scraper", "download", "downloader", "inspect", "inspector",
                 "fetch", "crawl", "probe", "debug", "tmp", "scratch", "demo", "example")


def is_scratch(path: str, temp_dir: str = "", root: str = "") -> bool:
    """A throwaway script: a scratch directory, the system temp dir, or a one-off tool name.

    Inside `root` (the project) only the part of the path below it is judged, so a
    project that lives under the system temp dir is not all scratch.
    """
    p = PurePath(path)
    if root and str(path).startswith(root):
        try:
            p = p.relative_to(root)
        except ValueError:
            pass
        temp_dir = ""
    parts = {part.lower() for part in p.parts[:-1]}
    if parts & _SCRATCH_DIRS:
        return True
    if temp_dir and str(path).startswith(temp_dir):
        return True
    stem = p.stem.lower()
    return any(stem == w or stem.startswith(w + "_") or stem.endswith("_" + w) for w in _SCRIPT_WORDS)


def is_library_code(path: str, temp_dir: str = "", root: str = "") -> bool:
    """Changed code that unit tests are about: not scratch, not an entry-point script."""
    return PurePath(path).name.lower() not in _ENTRY_POINTS and not is_scratch(path, temp_dir, root)


def project_has_tests(root: Path) -> bool:
    """Whether the project already has a test suite (a tests directory or test files)."""
    root = Path(root)
    try:
        for name in ("tests", "test", "__tests__", "spec"):
            if (root / name).is_dir():
                return True
        for pattern in ("test_*.py", "*_test.py", "*.test.js", "*.test.ts", "*.spec.js", "*.spec.ts", "*_test.go"):
            if next(root.glob(pattern), None) is not None:
                return True
        if (root / "pytest.ini").exists() or (root / "tox.ini").exists():
            return True
    except OSError:
        return False
    return False


# --- the shared budget -------------------------------------------------------------------

@dataclass
class ContinuationBudget:
    """One budget for every completion nudge in a request.

    ``limit`` continuations may be spent without progress. A continuation that
    produced real progress (a deliverable changed, a check passed, a part was
    addressed) hands its slot back, so a long task that is still advancing is not
    cut off; ``total`` caps the lifetime number so nothing loops forever. A limit
    of 0 turns the completion checks off.
    """

    limit: int = DEFAULT_CONTINUATIONS
    spent: int = 0
    total: int = 0
    unmet: list[str] = field(default_factory=list)
    _mark: object = None

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    @property
    def exhausted(self) -> bool:
        return (self.spent >= self.limit
                or self.total >= max(1, self.limit) * TOTAL_CONTINUATION_FACTOR)

    def observe(self, mark: object) -> None:
        """Compare the run's progress marker with the one taken at the last nudge."""
        if self._mark is not None and mark != self._mark and self.spent:
            self.spent -= 1
        self._mark = mark

    def spend(self, mark: object, reason: str = "") -> bool:
        """Use one continuation; False (and note why) when none is left."""
        if not self.enabled:
            return False
        self.observe(mark)
        if self.exhausted:
            if reason and reason not in self.unmet:
                self.unmet.append(reason)
            return False
        self.spent += 1
        self.total += 1
        self._mark = mark
        return True

    def unmet_note(self) -> str:
        return ("Not completed after " + str(self.total) + " follow-up check"
                + ("s" if self.total != 1 else "") + ": " + "; ".join(self.unmet) + ".") if self.unmet else ""


@dataclass
class Nudge:
    kind: str
    text: str
    unmet: str = ""  # what to tell the user if the budget runs out before this is met


def continue_text(problem: str, ask: str) -> str:
    """Every completion nudge reads as an instruction to carry on, never to stop."""
    return f"{problem} {ask}".strip()


UNFINISHED_ASK = (
    "Continue the task now and do not stop until the result works or you are blocked. "
    "If you are blocked, say exactly what is missing.")
EMPTY_REPLY_ASK = ("Your last reply was empty. Continue the task: call the next tool, or reply "
                   "in plain text if you are finished.")
VERIFY_ASK = ("Continue, and also run what you changed: inspect or test the changed result with one "
              "suitable command, fix anything that fails, then reply. If it cannot be run here, say so plainly.")


def outputs_nudge(missing: list[str]) -> Nudge:
    listed = ", ".join(missing[:6])
    return Nudge("outputs", continue_text(
        f"These files were required but are missing or empty: {listed}.",
        "Continue: create them now, or say plainly why they cannot be created."),
        unmet=f"missing or empty files: {listed}")


def subgoals_nudge(items: list[str]) -> Nudge:
    listed = "; ".join(f"({n}) {item}" for n, item in enumerate(items[:6], 1))
    return Nudge("subgoals", continue_text(
        f"Some parts of the request have not been addressed yet: {listed}.",
        "Continue with those parts. When a part is done, or cannot be done, say so in your reply."),
        unmet="parts of the request not addressed: " + "; ".join(items[:6]))


def tests_nudge(paths: list[str]) -> Nudge:
    return Nudge("tests", continue_text(
        "This project has a test suite, and these library files changed without tests: "
        + ", ".join(paths[:6]) + ".",
        "Continue: call generate_tests on each, or say why tests do not apply."),
        unmet="library files changed without tests: " + ", ".join(paths[:6]))

