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

import json
import os
import re
from itertools import islice
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath

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


# Phrases that contain an "unfinished" word but say the opposite or something
# else: "nothing left to do", "no remaining steps", advice to the user ("if you
# haven't installed it yet"), a summary announcement ("I will now explain"), and
# a file name (TODO.md).
_NOT_UNFINISHED = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:no|nothing|none|not\s+any|without|zero)\b[^.\n]{0,30}\b(?:remaining|left|outstanding|"
    r"to[- ]?do|unfinished|pending|more\s+to)\b",
    r"\bif\s+(?:you|your)\b[^.\n]*",
    r"\bi(?:['’]ll|\s+will)\s+now\s+(?:explain|summari[sz]e|describe|walk|show|list|recap|outline|present)\b",
    r"\blet\s+me\s+(?:now\s+)?(?:explain|summari[sz]e|describe|walk|show|list|recap|outline)\b",
    r"\bto[- ]?do(?:s)?(?=\.\w|[-_]list\b|\.md\b)",
    r"\bto[- ]?do\.\w+",
))


def admits_unfinished_work(reply: str) -> bool:
    """True if the reply says, in plain words, that the work is not finished."""
    text = _prose(reply)
    for pattern in _NOT_UNFINISHED:
        text = pattern.sub(" ", text)
    return any(pattern.search(text) for pattern in _UNFINISHED)


# A reply that says, in its own words, that a part of the request was not done: a "Not done" heading with something
# under it, or "I could not create / install / build ...". Used only to lower the verdict, never to send the model
# back to work. A limit on checking ("I could not verify it in a browser") is not one: the verification level says
# that already. A step that was blocked and done another way ("..., so I built it under artifacts/") is not one.
# "Not completed:" is left out: it is how the end-of-run notes list open parts, not the model's words.
_NOT_DONE_LABEL = re.compile(r"^[\W_]*not\s+(?:done|finished)\b[^\n:]{0,40}:[ \t]*(?P<rest>[^\n]*)",
                             re.IGNORECASE | re.MULTILINE)
_NOTHING = re.compile(r"^\W*(?:none|nothing|n/?a)\b", re.IGNORECASE)
_LIST_ITEM = re.compile(r"[ \t]*(?:[-*]|\d+[.)])\s+(?P<item>\S[^\n]*)")
_COULD_NOT_DO = re.compile(
    r"\b(?:i|we)\s+(?:could|was|were)\s*(?:n['’]?t|\s+not)\s+(?:be\s+)?(?:able\s+to\s+)?"
    r"(?:create|install|build|write|make|finish|complete|set\s+up|fix|add|produce|generate|implement)\b"
    r"|\b(?:i|we)\s+(?:am|was|were)\s+unable\s+to\s+"
    r"(?:create|install|build|write|make|finish|complete|set\s+up|fix|add|produce|generate|implement)\b",
    re.IGNORECASE)
_WORKED_AROUND = re.compile(r"(?:,?\s+so\s+i\b|\binstead\b|\bworked\s+around\b|\bby\s+(?:using|copying)\b)",
                            re.IGNORECASE)


# A sentence about checking the work, not doing it: "the click-through is the part I couldn't complete", "I could
# not finish the browser playthrough". Seen in a benchmark run whose page passed every grader check. The verification
# level already says what was not checked. "Tests" is left out on purpose: "I couldn't finish the tests" can be a
# part of the request that was not done.
_ABOUT_CHECKING = re.compile(
    r"\b(?:verif\w*|confirm\w*|click[- ]?through|play[- ]?through|walk[- ]?through|smoke\s+test|screenshot\w*|"
    r"(?:in|with)\s+a\s+(?:live\s+|real\s+)?browser|the\s+claim|end[- ]to[- ]end\s+check)\b",
    re.IGNORECASE)


def reports_part_not_done(reply: str) -> bool:
    """True when the reply itself says a part of what was asked was not done."""
    text = _prose(reply).replace("**", "").replace("__", "")
    text = re.sub(r"\bif\s+(?:you|your)\b[^.\n]*", " ", text, flags=re.IGNORECASE)
    for label in _NOT_DONE_LABEL.finditer(text):
        rest = label.group("rest").strip()
        if not rest:
            item = _LIST_ITEM.match(text, label.end() + 1)
            rest = item.group("item") if item else ""
        if rest and not _NOTHING.match(rest):
            return True
    for sentence in _SENTENCE_SPLIT.split(text):
        if (_COULD_NOT_DO.search(sentence) and not _WORKED_AROUND.search(sentence)
                and not _ABOUT_CHECKING.search(sentence)):
            return True
    return False


# A reply that says one of its own checks failed, and lets it stand. Seen in a benchmark run: the model's browser test
# showed a FAIL in a screenshot, which no check of the run can read. Its answer named the FAIL and called it "a
# harness timing artifact", and the run ended "verified" over a page whose remove button was broken. A failure the
# reply says was fixed ("it failed at first; after the fix it passes") is the checks' business, not this rule's.
# After "a" or "the" only an uppercase FAIL marker counts: "reproduce the failure" names the bug the task is about,
# not a check of the model's that failed (seen marking a passing fix NOT VERIFIED). A count ("2 failures") may be
# in any case.
_OWN_CHECK_FAILED = re.compile(
    r"\b(?:one|two|three|[1-9]\d*)\s+[`'\"]?FAIL(?:ED|S|URES?)?\b[`'\"]?"
    r"|\b(?:a|the)\s+[`'\"]?(?-i:FAIL(?:ED|S|URES?)?)\b[`'\"]?"
    r"|\b(?:tests?|checks?|assertions?|steps?|cases?|scenarios?)\s+(?:still\s+)?(?:failed|did\s*n[o']t\s+pass)\b"
    r"|\b(?:failed|failing)\s+(?:tests?|checks?|assertions?|steps?|cases?)\b"
    r"|\b(?:threw|raised)\s+(?:an?\s+)?(?:[\w-]+\s+){0,2}\w*(?:error|exception)\b"
    r"|\b[1-9]\d*\s+(?:of\s+\d+\s+)?(?:tests?|checks?|assertions?|cases?)\s+failed\b",
    re.IGNORECASE)
_NO_FAILURE = re.compile(
    r"\b(?:no|zero|0|none|without|never|nothing|not\s+(?:a\s+single|one|any))\b[^.\n]{0,30}"
    r"\b(?:fail\w*|errors?|exceptions?)\b"
    r"|\b(?:fail\w*|errors?|exceptions?)\b[^.\n]{0,15}\b(?:none|0|zero)\b"
    r"|\b(?:would|will|should|could|must|can)\s+(?:\w+\s+)?(?:fail|throw|raise)\b"
    # The harness's own note about a failing check, which the reply is answering, not a check of the model's.
    r"|\b(?:recorded|reported|flagged|latest|earlier|harness['’]s)\s+failing\s+check\b|^\W*failing\s+check\s*:",
    re.IGNORECASE)
_FAILURE_FIXED = re.compile(
    r"\b(?:fixed|corrected|resolved|repaired|after\s+the\s+(?:fix|change)|now\s+pass\w*|passe[sd]\s+now|"
    r"re-?ran|reran|on\s+the\s+(?:second|next|re-?)\s*(?:run|try)|then\s+pass\w*)\b",
    re.IGNORECASE)


# "the failing test" or "the previously failing one" names the test the task was about, which the fix makes pass:
# not a check of the model's that failed (seen marking a passing fix NOT VERIFIED). A sentence that says it still
# fails keeps the phrase.
_TASK_FAILING_TEST = re.compile(
    r"\b(?:the|your|this|that)\s+(?:(?:previously|originally|given|one)\s+)?failing\s+"
    r"(?:tests?|checks?|ones?|cases?)\b",
    re.IGNORECASE)
_STILL = re.compile(r"\b(?:still|remains?|again)\b", re.IGNORECASE)
# A heading over the reply's account of the bug it was asked to fix ("What was wrong", "Root cause", "The bug").
# What that section says failed is the starting state, not a check of the model's (seen: "The link step failed
# because ..." under "What was wrong", on a build whose full run passed). A heading is a markdown heading or a line
# that is only bold text.
_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s*(?P<md>.+?)\s*#*|\*\*(?P<bold>[^*]+?)\*\*:?)\s*$")
_CAUSE_HEADING = re.compile(
    r"^\W*(?:what\s+(?:was|went)\s+(?:wrong|broken|failing)|(?:the\s+)?(?:root\s+)?causes?|"
    r"(?:the\s+)?(?:bug|problem|issue|failure)s?|why\s+it\s+(?:failed|broke)|before\s+the\s+(?:fix|change)|"
    r"diagnosis|what\s+i\s+found)\b",
    re.IGNORECASE)


def _without_cause_sections(text: str) -> str:
    """The reply without the lines under a heading about the original bug, except a line that says it still fails."""
    kept, in_cause = [], False
    for line in text.splitlines():
        heading = _HEADING_LINE.match(line)
        if heading:
            in_cause = bool(_CAUSE_HEADING.match(heading.group("md") or heading.group("bold") or ""))
            kept.append("")
            continue
        kept.append(line if not in_cause or _STILL.search(line) else "")
    return "\n".join(kept)


def reports_failed_check(reply: str) -> str:
    """The sentence in which the reply says one of its own checks failed and it was not fixed, or ""."""
    text = _without_cause_sections(_prose(reply)).replace("**", "").replace("__", "")
    text = re.sub(r"\bif\s+(?:you|your|it|the)\b[^.\n]*", " ", text, flags=re.IGNORECASE)
    for sentence in _SENTENCE_SPLIT.split(text):
        if not _STILL.search(sentence):
            sentence = _TASK_FAILING_TEST.sub(" ", sentence)
        if (_OWN_CHECK_FAILED.search(sentence) and not _NO_FAILURE.search(sentence)
                and not _FAILURE_FIXED.search(sentence)):
            return " ".join(sentence.split())
    return ""


_ADVICE_START = re.compile(
    r"^\W*(?:how|why|what|which|where|when|who|is|are|does|do|did|should|explain|describe|"
    r"tell\s+me|show\s+me\s+how|walk\s+me|what['’]s)\b", re.IGNORECASE)
_ACTION_ASK = re.compile(r"^\W*(?:please\s+)?(?:can|could|would|will)\s+you\b", re.IGNORECASE)


def is_advice_request(request: str) -> bool:
    """A question or an "explain" request: the user wants an answer, not an edit.

    "how do I fix X?" asks for advice; "can you fix X?" and "fix X" ask for the
    change.
    """
    text = (request or "").strip()
    first = _SENTENCE_SPLIT.split(_prose(text))[0] if text else ""
    if _ACTION_ASK.match(first):
        return False
    return bool(_ADVICE_START.match(first))


# --- 1a. A reply that only says what the model will do next ----------------------------

# "Let me read the data file first." with no tool call does none of the work. Seen in benchmark runs: the reply
# went to the completion checks as if it were an answer, each check got another sentence like it, and the run
# ended in 16 seconds with nothing done.
_NEXT_STEP_START = re.compile(
    r"^\W*(?:(?:ok(?:ay)?|sure|alright|great|got\s+it|understood)\W+)?"
    r"(?:(?:first|now|next|then|to\s+start|to\s+begin)\W+)?"
    r"(?:let\s+me|let['’]s|i['’]ll|i\s+will|i\s+am\s+going\s+to|i['’]m\s+going\s+to|i\s+need\s+to|"
    r"i\s+should|i['’]m\s+(?:now\s+)?(?:starting|going)|starting\s+(?:with|by))\b", re.IGNORECASE)
NEXT_STEP_MAX_CHARS = 400


def only_announces_next_step(reply: str) -> bool:
    """A short reply that only says what the model is about to do ("Let me read the data first.",
    "I'll build the page, then check it."). A longer reply, or one with code in it, may be a real answer."""
    text = (reply or "").strip()
    if not text or len(text) > NEXT_STEP_MAX_CHARS or "```" in text:
        return False
    return bool(_NEXT_STEP_START.match(text))


_WORK_VERB = re.compile(
    r"\b(?:build|create|make|implement|develop|write|generate|design|code|produce|set\s+up|fix|repair|patch|"
    r"add|update|change|edit|refactor|rename|remove|delete|install|run|start|serve|deploy|test|debug|convert|"
    r"migrate|port|configure|bundle|compile|render|upgrade|clean|organi[sz]e|download|scrape|analy[sz]e)\b",
    re.IGNORECASE)


def asks_for_work(request: str) -> bool:
    """A request to do something with the tools ("build the page", "fix module_077"), not a greeting or a
    question. A request that names an output file asks for work whatever its verb."""
    if is_advice_request(request):
        return False
    text = _prose(request or "")
    return bool(_WORK_VERB.search(text) or named_output_paths(request or ""))


NOT_STARTED_ASK = ("No tool has been called yet, so none of the task is done. Start now: call the tool for the "
                   "first step. A reply that only says what you will do does not do it.")


# --- 1b. A reply that hands the work back to the user --------------------------------

# These phrases ask the user to do, personally, a step the agent's own tools
# cover: install, run, set up and configure are shell or file operations, so
# "please install Node and run npm start" hands the task back. Deliberately
# absent: what only a human can supply ("enter your API key", "log in to your
# account") and mere suggestions ("you could also run..."). A finished answer
# may end with usage instructions that use the same verbs, which is why the
# engine counts a hand-off only when the run itself shows the work is not done
# (see _reply_hands_back in engine.py).
_HANDS_BACK = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bplease\s+(?:first\s+)?(?:install|run|execute|start|launch|set\s+up|configure|"
    r"download|deploy)\b",
    r"\byou\s+(?:will\s+need|need|have|must|should)(?:\s+to)?\s+(?:first\s+)?"
    r"(?:install|run|execute|start|launch|set\s+up|configure|download|deploy)\b",
    r"\byou(?:['’]ll| will| would|['’]d)\s+(?:need|have)\s+to\s+"
    r"(?:install|run|execute|start|launch|set\s+up|configure|download|deploy)\b",
    r"\binstall\b[^\n]{0,60}?\b(?:and|then)\s+(?:run|start|launch)\b",
))


def hands_work_back(reply: str) -> bool:
    """True if the reply tells the user to do, themselves, work the agent could
    have done with its own tools.

    Wording alone does not decide: the engine treats a hand-off as unfinished
    only when the run also shows the work is not done, so setup or usage
    instructions at the end of a finished answer cost nothing.
    """
    return any(pattern.search(_prose(reply)) for pattern in _HANDS_BACK)


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


# A sentence that opens with a condition: its main clause, after the first comma, is only asked for under it.
_OPENING_CONDITION = re.compile(
    r"^\W*(?:(?:and|also|then|finally)\s+)?(?:if|unless|in\s+case|only\s+if|provided\s+that)\b", re.IGNORECASE)
# A verb offered as a choice rather than asked for: "optionally write", "you may also save", "feel free to create".
_OFFERED_AS_CHOICE = re.compile(
    r"\b(?:optionally|optional|(?:you\s+)?(?:may|might|can|could)(?:\s+also)?|feel\s+free\s+to|"
    r"if\s+(?:needed|necessary|helpful|useful|you\s+(?:want|like|wish)))\s*(?:also\s+)?(?:to\s+)?$", re.IGNORECASE)


def _is_optional_file(sentence: str, path_start: int, cue_start: int) -> bool:
    """The file is named in a clause that only asks for it under a condition, or offers it as a choice.

    A file the request makes optional cannot be called missing: whether it was due depends on something the check
    cannot see (did the conversation become long?). Only two shapes count, so a condition elsewhere in the sentence
    does not hide a required file: the sentence opens with the condition and the file is in what follows it, or the
    verb that names the file is itself offered as a choice. A time clause ("When done, ...") is neither.
    """
    if _OPENING_CONDITION.match(sentence):
        comma = sentence.find(",")
        if comma < 0 or path_start > comma:
            return True
    return bool(_OFFERED_AS_CHOICE.search(sentence[max(0, cue_start - 40):cue_start]))


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
            cues = list(_OUTPUT_CUE.finditer(before))
            output = [m.end() for m in cues]
            inputs = [m.end() for m in _INPUT_CUE.finditer(before)]
            if not output or (inputs and max(inputs) > max(output)):
                continue
            if _is_optional_file(sentence, match.start(), cues[-1].start()):
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


def _dir_word_name(match) -> str:
    """The folder name after "folder", "directory" or "dir", or "" when there is none.

    The pattern takes the next word, whatever it is. Seen in a benchmark run: "in the current folder with the header"
    made the run look for a folder called `with/`. A plain word ("with", "that", "for") is a name only when quoted
    ("a folder called `with`"). A full stop that ends the sentence is not part of the name ("named results.")."""
    name = match.group(1).rstrip(".")
    quoted = match.group(0)[match.start(1) - match.start(0) - 1:match.start(1) - match.start(0)] in ("`", "'", '"')
    if not quoted and name.lower() in _STOPWORDS:
        return ""
    return name


def named_output_dirs(request: str) -> list[str]:
    """Directories the request asks to be filled (`out/`, "a folder named results")."""
    found: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(_prose(request)):
        spots = [(m.start(), m.group(1).rstrip("/\\")) for m in _DIR_TOKEN.finditer(sentence)]
        spots += [(m.start(), _dir_word_name(m)) for m in _DIR_WORD.finditer(sentence)]
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


# --- files that were already there ----------------------------------------------------
#
# A request often names a file it supplies ("the format is in example.json"). The
# word "export" or "save" earlier in the same sentence made such a file look like
# something to produce. Two independent facts are needed before a named file is
# treated as supplied: it was there when the request started and is unchanged and
# unwritten now (measured), and every place the request mentions it reads like a
# reference to existing material (worded). Either alone is not enough: a stale
# file with an output's name is exactly what the missing-file check exists to catch.

INVENTORY_LIMIT = 400        # files recorded at the start of a request
INVENTORY_VISIT_LIMIT = 4000  # directory entries looked at, so a huge tree cannot stall the start
INVENTORY_DEPTH = 3
_INVENTORY_SKIP = frozenset({"node_modules", ".git", ".venv", "venv", "__pycache__", ".tox",
                             ".mypy_cache", ".pytest_cache", "dist", "build"})


def _file_id(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def inventory(roots: Iterable[Path]) -> dict[str, list[int]]:
    """Size and modification time of the files under `roots`, bounded in count and depth."""
    found: dict[str, list[int]] = {}
    visited = 0
    for root in roots:
        stack = [(Path(root), 0)]
        while stack and len(found) < INVENTORY_LIMIT and visited < INVENTORY_VISIT_LIMIT:
            folder, depth = stack.pop()
            # Read entries lazily and stop once the budget is spent. Slicing the
            # listing to the number of files still needed counted folders
            # against it too, so a skipped folder such as node_modules in that
            # slice left the inventory short of files that were there.
            try:
                scan = os.scandir(folder)
            except OSError:
                continue
            with scan as entries:
                for entry in entries:
                    visited += 1
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < INVENTORY_DEPTH and entry.name not in _INVENTORY_SKIP:
                                stack.append((Path(entry.path), depth + 1))
                        elif entry.is_file(follow_symlinks=False):
                            info = entry.stat()
                            found.setdefault(_file_id(entry.path), [info.st_size, info.st_mtime_ns])
                    except OSError:
                        pass
                    # Checked after the entry, so no entry past the budget is read.
                    if len(found) >= INVENTORY_LIMIT or visited >= INVENTORY_VISIT_LIMIT:
                        break
    return found


_INPUT_REFERENCE = re.compile(
    r"\b(?:(?:is|are|was|were)\s+(?:already\s+)?(?:in|given\s+in|described\s+in|listed\s+in|shown\s+in|"
    r"defined\s+in|documented\s+in|inside)|see|described\s+in|listed\s+in|shown\s+in|defined\s+in|"
    r"documented\s+in|as\s+in|like\s+in|follows?|matches|matching)\b",
    re.IGNORECASE)


_CLAUSE_BREAK = re.compile(r"[,;()]|\band\b|\bthen\b|\bbut\b", re.IGNORECASE)


def is_input_reference(request: str, path: str) -> bool:
    """Whether every mention of `path` in the request reads like a pointer to existing material.

    "(the format is in spec.json)" and "see spec.json" do; "save it to spec.json" does not.
    A path with no mention at all is not a reference.
    """
    mentions = 0
    for sentence in _SENTENCE_SPLIT.split(_prose(request)):
        start = sentence.find(path)
        while start != -1:
            mentions += 1
            before = sentence[:start]
            # Only the clause that holds the path counts: "(see notes.md) and write spec.json"
            # points at notes.md, not at spec.json.
            clause = _CLAUSE_BREAK.split(before)[-1]
            if not _INPUT_REFERENCE.search(clause):
                return False
            start = sentence.find(path, start + len(path))
    return mentions > 0


def supplied_input(request: str, path: str, candidates: Iterable[Path],
                   start_files: dict | None, written: Iterable[str],
                   roots: Iterable[Path]) -> bool:
    """A named file that was there at the start, is unchanged, was never written, and is only
    mentioned as existing material."""
    if not start_files or not is_input_reference(request, path):
        return False
    written_ids = set()
    for item in written:
        p = Path(os.path.expanduser(str(item)))
        written_ids.add(_file_id(p))
        if not p.is_absolute():
            written_ids.update(_file_id(Path(root) / p) for root in roots)
    for candidate in candidates:
        before = start_files.get(_file_id(candidate))
        if before is None or _file_id(candidate) in written_ids:
            continue
        try:
            info = candidate.stat()
        except OSError:
            continue
        if [info.st_size, info.st_mtime_ns] == list(before):
            return True
    return False


def _ids_of(path: str, roots: Iterable[Path]) -> set:
    return {_file_id(c) for c in _candidates(path, roots)} | {_file_id(path)}


def _landed_copy(path: str, landed: Iterable, roots: Iterable[Path]) -> bool:
    """Whether a write to `path` was diverted and the file now exists where it landed.

    The write tool keeps files the sandbox cannot reach (anything outside the artifacts folder)
    in the artifacts folder and reports both places. The file exists; the end of the run
    puts a copy back at the path that was asked for.
    """
    wanted = _ids_of(path, roots)
    for pair in landed:
        try:
            requested, got = pair[0], pair[1]
        except (TypeError, IndexError):
            continue
        if wanted & _ids_of(str(requested), roots) and _nonempty_file(Path(str(got))):
            return True
    return False


def _landed_under(folder: Path, landed: Iterable, roots: Iterable[Path]) -> bool:
    """Whether a diverted write that was meant for a file inside `folder` now exists."""
    prefix = _file_id(folder) + os.sep
    for pair in landed:
        try:
            requested, got = str(pair[0]), Path(str(pair[1]))
        except (TypeError, IndexError):
            continue
        if any(i.startswith(prefix) for i in _ids_of(requested, roots)) and _nonempty_file(got):
            return True
    return False


def _named_folders(request: str) -> set[str]:
    """Full output-directory paths, not input directories or isolated path segments."""
    names = set(named_output_dirs(request))
    for path in named_output_paths(request):
        parent = str(PurePath(path.replace("\\", "/")).parent)
        if parent != ".":
            names.add(parent)
    return {name.replace("\\", "/").rstrip("/") for name in names
            if not Path(name).is_absolute() and ".." not in PurePath(name.replace("\\", "/")).parts
            and name not in (".", "", "~")}


def _written_in_named_folder(path: str, request: str, candidates: Iterable[str],
                             roots: Iterable[Path]) -> bool:
    """The request names `path` (server.js) and, elsewhere, the project folder it belongs to
    (pokemon-doubles/); the run wrote that file inside that folder.

    A plan lists the files of a project folder by their short names, so a check that looks for
    them at the top of the workspace calls them missing while they sit in the folder the plan
    named. The folder must be one the request itself names: a file written under an unrelated
    folder does not count.
    """
    # Explicit root/absolute paths are not shorthand for a project-relative output.
    normal = path.replace("\\", "/")
    if normal.startswith(("./", "../", "~/")) or Path(path).is_absolute():
        return False
    folders = _named_folders(request)
    if not normal or not folders:
        return False
    expected = {_file_id(Path(root) / folder / normal) for root in roots for folder in folders}
    for candidate in candidates:
        if any(_file_id(c) in expected and _nonempty_file(c) for c in _candidates(str(candidate), roots)):
            return True
    return False


def _written_under_named_dir(folder: str, request: str, candidates: Iterable[str],
                             roots: Iterable[Path], mirrors: Iterable[Path]) -> bool:
    """A folder the request names (proj/, public/) holds files the run wrote, one level inside the
    artifacts folder or inside another folder the request names."""
    normal = folder.replace("\\", "/")
    if not normal or normal.startswith(("./", "../", "~/")) or Path(folder).is_absolute():
        return False
    # Anchor every match to an allowed workspace/mirror root; a directory with the
    # same basename somewhere else is not evidence that this output exists.
    roots = list(roots) + list(mirrors)
    parents = _named_folders(request) | {""}
    expected = {_file_id(Path(root) / parent / normal) + os.sep for root in roots for parent in parents}
    for candidate in candidates:
        for item in _candidates(str(candidate), roots):
            if any(_file_id(item).startswith(prefix) for prefix in expected) and _nonempty_file(item):
                return True
    return False


def _moved_copy(path: str, written: Iterable[str], roots: Iterable[Path]) -> bool:
    """A file the run wrote at one path and then moved: the same name exists, non-empty, at another path it wrote."""
    name = PurePath(str(path).replace("\\", "/")).name.lower()
    return any(other != path and PurePath(str(other).replace("\\", "/")).name.lower() == name
               and _exists_somewhere(str(other), roots) for other in written)


def missing_outputs(request: str, reply: str, written: Iterable[str],
                    roots: Iterable[Path], mirror_roots: Iterable[Path] = (),
                    start_files: dict | None = None, landed: Iterable = (),
                    removed: Iterable[str] = ()) -> list[str]:
    """Named or claimed deliverables that do not exist, are empty, or sit in the
    wrong place.

    Three sources: paths the request asked for, directories it asked to be
    filled, and paths this run wrote that the reply names as its result (an empty
    or removed file is not one; `removed` names the files the run deleted itself). `mirror_roots` (the artifacts folder) is where
    unspecified work goes by default; a requested path found only there is
    reported as misplaced rather than satisfied. The check is deterministic and
    makes no model call.
    """
    roots = [Path(r) for r in roots]
    mirrors = [Path(r) for r in mirror_roots]
    written = list(written)
    landed = list(landed)
    missing: list[str] = []
    wrote_at = written + [str(pair[1]) for pair in landed if len(pair) > 1]
    for path in named_output_paths(request):
        satisfied = (any(_nonempty_file(c) for c in _candidates(path, roots))
                     or _landed_copy(path, landed, roots + mirrors)
                     or _written_in_named_folder(path, request, wrote_at, roots + mirrors))
        if not satisfied and supplied_input(request, path, _candidates(path, roots + mirrors),
                                            start_files, written, roots + mirrors):
            continue
        if not satisfied:
            elsewhere = any(_nonempty_file(c) for c in _candidates(path, mirrors))
            missing.append(f"{path} (found only in {mirrors[0]}; create it at the requested path)"
                           if elsewhere and mirrors else path)
    for path in named_output_dirs(request):
        if not any(_has_content(c) or _landed_under(c, landed, roots + mirrors)
                   for c in _candidates(path, roots)) \
                and not _written_under_named_dir(path, request, wrote_at, roots + mirrors, mirrors):
            missing.append(f"{path}/ (missing or empty)")
    lowered = (reply or "").lower()
    removed_names = {str(n).lower() for n in removed}
    for path in written:
        name = PurePath(path).name
        if (name.lower() in lowered and not _exists_somewhere(path, roots + mirrors)
                and not _landed_copy(path, landed, roots + mirrors) and path not in missing):
            # A scratch file the run made and removed, or a file it moved to a better place, is not a
            # deliverable that went missing.
            if (name.startswith("_") or name.lower() in removed_names
                    or _moved_copy(path, written, roots + mirrors)
                    or is_throwaway_helper(path, request, written, roots + mirrors)):
                continue
            if not any(PurePath(m.replace("\\", "/")).name.startswith(name) for m in missing):
                missing.append(path)
    missing.extend(_claimed_but_unwritten(lowered, written, roots + mirrors))
    return missing


# A delete the run ran itself: `rm -f artifacts/shim.py`, `del x.py`, `Remove-Item x.py`. Any word boundary starts
# one, because the saved command lines are flattened: a script's second line follows its first after a space.
_DELETE_STEP = re.compile(r"(?<![\w./\\-])(?:rm|del|erase|remove-item|ri)(?:\.exe)?\s+([^;&|\n]+)", re.IGNORECASE)


def removed_by_commands(ran: Iterable[str]) -> set[str]:
    """Names of the files the run's own finished commands deleted. Seen in a run: a fallback module the run wrote, then
    deleted once the real package installed, was reported as a missing file because the reply mentioned it."""
    names = set()
    for command in ran or ():
        for match in _DELETE_STEP.finditer(str(command)):
            for word in match.group(1).split():
                word = word.strip("\"'")
                if word and not word.startswith("-") and "*" not in word and "?" not in word:
                    names.add(PurePath(word.replace("\\", "/")).name.lower())
    return names


def _mirror_equivalent(path: str, roots: Iterable[Path], mirrors: Iterable[Path]) -> Path | None:
    """The same path inside a mirror root (the artifacts folder), when it holds something there.

    A run's own writes land in the artifacts folder by default, so a folder a plan names at the project root can
    hold the finished work one level over. The path may be absolute (the plan spelled it out) or relative."""
    p = Path(os.path.expanduser(path))
    relatives: list[Path] = []
    if p.is_absolute():
        for root in roots:
            try:
                relatives.append(p.resolve().relative_to(Path(root).resolve()))
            except (OSError, RuntimeError, ValueError):
                continue
    else:
        relatives = [p]
    for relative in relatives:
        for mirror in mirrors:
            try:
                mirror_root = Path(mirror).resolve()
                candidate = (mirror_root / relative).resolve()
                candidate.relative_to(mirror_root)
            except (OSError, RuntimeError, ValueError):
                continue  # traversal or a link outside the mirror is not artifact content
            if _has_content(candidate) or _nonempty_file(candidate):
                return candidate
    return None


def annotate_missing(missing: list[str], roots: Iterable[Path], mirrors: Iterable[Path]) -> list[str]:
    """Add "its content is at <place>" to a missing output whose content exists in the mirror folder.

    Without it the nudge only says the path is missing, and a model that knows it built the thing in the artifacts
    folder spends rounds looking for what the controller means. Entries that already say where the file is found are
    left alone."""
    roots, mirrors = list(roots), list(mirrors)
    out = []
    for entry in missing:
        if "found only in" in entry or "its content is at" in entry:
            out.append(entry)
            continue
        if entry.endswith("/ (missing or empty)"):
            base = entry[: -len("/ (missing or empty)")]
            found = _mirror_equivalent(base, roots, mirrors)
            out.append(entry.replace("(missing or empty)", f"(missing or empty; but its content is at {found})")
                       if found else entry)
        elif " (" not in entry:
            found = _mirror_equivalent(entry, roots, mirrors)
            out.append(f"{entry} (its content is at {found})" if found else entry)
        else:
            out.append(entry)
    return out


# A test file is named in the reply but was never written. Narrow on purpose:
# only conventional test filenames, because any reply mentioning any filename
# would otherwise trip this and a check that fires on ordinary prose is worse
# than no check at all.
# A name needs an extension to be a file: "test-hook" and "test-assertion" are words in prose or in a
# page's data attributes ("data-test-hook"), and "test_board.py" is a file.
_TEST_FILE_RE = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+[\\/])*(?:test[_-][\w-]+\.[a-z]{1,5}|[\w-]+[_-]test\.[a-z]{1,5}|"
    r"[\w.-]+\.(?:spec|test)\.[a-z]{2,5}))(?![\w-]|\.\w)", re.IGNORECASE)


def _claimed_but_unwritten(lowered_reply: str, written, search_roots) -> list[str]:
    """Test files the answer claims but that are nowhere on disk.

    missing_outputs already covers files this run wrote and then lost; this
    covers the opposite order - the model asserts a test exists that it never
    created, which is how "I added three tests" reaches the user as fact.
    """
    known = {PurePath(str(p).replace("\\", "/")).name.lower() for p in written}
    out: list[str] = []
    for match in _TEST_FILE_RE.finditer(lowered_reply):
        name = match.group(1)
        base = PurePath(name.replace("\\", "/")).name
        if base in known or base in out:
            continue
        if any(_nonempty_file(c) for c in _candidates(name, search_roots)):
            continue
        out.append(base)
    return out


def _exists_somewhere(path: str, roots: Iterable[Path]) -> bool:
    """A written path may be recorded as the model spelled it, relative to a workspace.

    A path the run wrote as a file and that is now a folder holding files (a build that makes out/ where a stray out
    file was) exists too."""
    return any(_nonempty_file(c) or _has_content(c) for c in _candidates(path, roots))


# --- 4. Parts of a multi-part request --------------------------------------------------

_LIST_LINE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(\S.*)$")
_CLAUSE_SPLIT = re.compile(
    r"(?:[,;.]|\band\b)\s*(?:and\s+)?(?:then|also|after\s+that|afterwards|finally)\b[,:]?\s+",
    re.IGNORECASE)
_STOPWORDS = frozenset(
    "the a an and or of to in on for with that this it is are be as at by from into then also "
    "make sure should must can will would your you their there each every all any some please "
    "using use when where which what have has had been being not only just than more most".split())


# A request written as a paragraph often states its parts as separate "it should ... /
# players must be able to ..." sentences. Three or more of them are a checklist.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_REQUIREMENT_SENTENCE = re.compile(
    r"\b(?:should|must|need(?:s)? to|has to|have to|allow(?:s)?|let(?:s)?|able to|support(?:s)?|"
    r"include(?:s)?|provide(?:s)?|display(?:s)?|show(?:s)?|consider(?:s)?|ensure(?:s)?|"
    r"make sure|require(?:s|d)?)\b", re.IGNORECASE)
MIN_REQUIREMENT_SENTENCES = 3


def extract_subgoals(request: str, sentences: bool = True) -> list[str]:
    """The separate parts of a multi-part request, or [] when it is a single ask.

    Built from a numbered or bulleted list, from explicit "and also / then" clauses,
    or (``sentences``) from three or more sentences that each state a requirement
    ("it should ...", "players must be able to ..."). Fewer than two items is not a
    checklist, so a plain request costs nothing. A request with none of that
    structure builds no checklist, and its completion relies on the other checks.
    """
    text = _prose(request).strip()
    if not text:
        return []
    items = [m.group(1).strip() for line in text.splitlines()
             if (m := _LIST_LINE.match(line))]
    if len(items) < 2:
        parts = [p.strip(" ,;.") for p in _CLAUSE_SPLIT.split(text)]
        items = [p for p in parts if len(p.split()) >= 3]
        # A clause that runs across a sentence or a paragraph is prose that happened to contain "then", not one part of
        # a list: no file can show a paragraph of description, so such a split is not a checklist.
        if len(items) < 2 or any("\n" in p or _SENTENCE_SPLIT.search(p) for p in items):
            items = []
    if not items and sentences:
        flat = " ".join(text.split())
        marked = [s.strip(" ,;.") for s in _SENTENCE_SPLIT.split(flat)
                  if len(s.split()) >= 4 and _REQUIREMENT_SENTENCE.search(s)]
        if len(marked) >= MIN_REQUIREMENT_SENTENCES:
            items = marked
    return [i[:160] for i in items[:MAX_SUBGOALS]]


# What a part of a request asks for, for the checklist. Order matters: a part that asks for tests is a test item even
# when it names the file the tests go in, and one that asks for a click or a key is interactive even when it says run.
_TEST_ITEM = re.compile(r"\b(?:unit[- ]?tests?|tests?|testing|pytest|jest|vitest|spec)\b", re.IGNORECASE)
_INTERACTIVE_ITEM = re.compile(
    r"\b(?:click(?:s|ed|ing)?|press(?:es|ed|ing)?|tap(?:s|ped|ping)?|drag(?:s|ged|ging)?|type(?:s|d)? into|"
    r"keys?|keyboard|arrow|mouse|button|play(?:s|ed|ing|able)?|player|game)\b", re.IGNORECASE)
_RUN_ITEM = re.compile(r"\b(?:run(?:s|ning)?|start(?:s|ed|ing)?|serve(?:s|d)?|launch(?:es|ed)?|install(?:s|ed)?|"
                       r"execute(?:s|d)?|deploy(?:s|ed)?|boot(?:s|ed)?)\b", re.IGNORECASE)


def item_kind(item: str) -> str:
    """test | interactive | file | run | property: what kind of evidence can settle this part of a request.

    file, run, test and interactive parts can be shown by a check (a file present, a command or test run that passed, a
    played interaction); a property ("keep the layout tidy") only by the work itself."""
    text = item or ""
    if _TEST_ITEM.search(text):
        return "test"
    if named_output_paths(text) or _FILE_NAME_IN_ITEM.search(text):
        return "file"   # "draw the board in game.html" asks for a file, whatever the file is for
    if _INTERACTIVE_ITEM.search(text):
        return "interactive"
    if _RUN_ITEM.search(text):
        return "run"
    return "property"


# A file named in a part ("in game.html", "data/expenses.json"), with or without a verb that writes it.
_FILE_NAME_IN_ITEM = re.compile(
    r"(?<![\w/@.-])[\w-]+(?:[/\\][\w.-]+)*\.(?:html?|css|m?js|cjs|jsx|tsx?|py|json|csv|md|txt|ya?ml|xml|svg|sh|"
    r"toml|ini|cfg|sql|go|rs|java|rb|php|c|cpp|h)\b", re.IGNORECASE)

# Kinds whose evidence is a run of the work: a later change makes it stale.
RERUN_KINDS = frozenset(("run", "test", "interactive"))


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _keywords(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9_]{4,}", text.lower()) if w not in _STOPWORDS}


_NO_TEXT_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".7z",
    ".sqlite", ".db", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".wav", ".docx", ".xlsx",
    ".pptx", ".pyc", ".exe", ".dll", ".so", ".bin", ".lock"})
_NO_TEXT_DIRS = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build"})


def deliverable_text(written: Iterable[str], roots: Iterable[Path],
                     per_file: int = 200_000, total: int = 600_000) -> str:
    """The text of the readable files a run wrote, capped, or "" when there is none.

    This is the evidence that a part of the request was built: what the files say,
    not what the reply says about them. Binary files, dependency folders and very
    large files are skipped, and the total is bounded.
    """
    roots = [Path(r) for r in roots]
    chunks, used, seen = [], 0, set()
    for raw in written:
        for candidate in _candidates(raw, roots):
            try:
                resolved = candidate.resolve()
                if (not candidate.is_file() or resolved in seen
                        or candidate.suffix.lower() in _NO_TEXT_SUFFIXES
                        or _NO_TEXT_DIRS & {p.lower() for p in resolved.parts}):
                    continue
                seen.add(resolved)
                with open(candidate, "r", encoding="utf-8", errors="ignore") as handle:
                    text = handle.read(per_file)
            except OSError:
                continue
            chunks.append(text)
            used += len(text)
            break
        if used >= total:
            break
    return "\n".join(chunks)[:total]


def _words_for_evidence(text: str) -> str:
    """Lower-case words, with camelCase, snake_case and kebab-case split, and every other non-word character a space.

    A key word is matched as the start of a word (" " + root), so a word that followed a quote, a slash or an equals
    sign was never found: in a package.json, `"build"`, `"dayjs"`, `src/index.js` and `--outfile=dist/app.js` all
    read as missing, and finished parts were reported as not addressed until the completion checks ran out."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return re.sub(r"[^a-z0-9]+", " ", spaced.lower())


# A part that asks the agent to run something, not to build something: it is met by a command that ran, which no file
# shows ("Run `npm run build`, then run `node dist/app.js`", "install the dependencies", "start the server").
_ACTION_PART = re.compile(
    r"^\W*(?:\d+[.)]\s*)?(?:and\s+|also\s+|then\s+|finally\s+)?(?:run|execute|start|launch|install|test|check|verify|"
    r"try|call|invoke|serve|open|load|curl|ping|restart|rerun|re-run)\b", re.IGNORECASE)


# A part that also asks for something to be installed or run, anywhere in it ("Create a virtual environment in `.venv`
# and install `tabulate`"): the things it names may be made by a command.
_ACTION_WORD = re.compile(r"\b(?:run|execute|start|launch|install|serve|restart|rerun|re-run)\b", re.IGNORECASE)
_NAMED = re.compile(r"`([^`\n]+)`")


# An example of what the page shows ("amounts with two decimals, like `$12.34`") is a value the running work prints,
# not a name its files hold. Seen in a run: `$12.34` was the one named thing the finished page did not contain.
_EXAMPLE_VALUE = re.compile(
    r"\b(?:like|e\.g\.,?|such\s+as|for\s+example,?|for\s+instance,?)\s*`([^`\n\w]*\d[^`\n]*)`", re.IGNORECASE)


# A part that asks for state to outlast a reload is met by a page that keeps it in browser storage. No file says
# "still there after the page is reloaded": seen in a run, the page saved the cart in localStorage, a play test
# reloaded it, and the part stayed open because the code never used the words "reload" or "still".
_PERSIST_PART = re.compile(
    r"\b(?:(?:still\s+there|kept|kept\s+safe|remembered|restored|saved|persist(?:s|ed)?)\b[^.]{0,60}?"
    r"\b(?:reload(?:s|ed|ing)?|refresh(?:es|ed|ing)?)\b"
    r"|(?:reload(?:s|ed|ing)?|refresh(?:es|ed|ing)?)\b[^.]{0,60}?\b(?:still\s+there|kept|remembered|restored|persist))",
    re.IGNORECASE)
_STORAGE_API = re.compile(r"\b(?:localStorage|sessionStorage|indexedDB)\b")


def _named_things(item: str) -> list[str]:
    examples = {m.start(1) for m in _EXAMPLE_VALUE.finditer(item)
                if not re.search(r"[A-Za-z]", m.group(1))}
    return [m.group(1) for m in _NAMED.finditer(item) if m.start(1) not in examples]


def _named_things_found(item: str, *haystacks: str) -> bool:
    """Whether every thing the part names in backticks (`.venv`, `--verbose`, `cli.py`) shows up, word for word.

    The named things are the checkable core of a part, so when each of them is there the part is not forgotten, even
    when the rest of it is described in other words.
    """
    named = [_words_for_evidence(n).split() for n in _named_things(item)]
    named = [words for words in named if words]
    return bool(named) and all(
        any(all(f" {w} " in hay for w in words) for hay in haystacks) for words in named)


# A name in backticks that is a path: a dot folder (`.venv`), a path with a slash (`out/app.bin`) or a file name
# (`totals.json`). A command (`npm run build`), a flag (`--verbose`) or a package (`tabulate`) is not one.
_DOT_FOLDER = re.compile(r"^\.[\w-][\w.-]*[\\/]?$")
_SLASH_PATH = re.compile(r"^[\w.-]+(?:[\\/][\w.-]+)+[\\/]?$")
_REMOVAL = re.compile(r"\b(?:delete|remove|drop|uninstall|rename|move|clean\s+up|get\s+rid\s+of)\b", re.IGNORECASE)
_MAKES = re.compile(r"\b(?:exists?)\b", re.IGNORECASE)


def _named_paths(item: str) -> list[str]:
    paths = []
    for name in (n.strip() for n in _NAMED.findall(item)):
        if " " in name or PurePath(name).is_absolute() or PureWindowsPath(name).is_absolute():
            continue
        if _DOT_FOLDER.match(name) or _SLASH_PATH.match(name) or _PATH_TOKEN.fullmatch(name):
            paths.append(name.rstrip("/\\"))
    return paths


def _named_path_missing(item: str, roots: list[Path]) -> bool:
    """A part asks for a path to be made (`Create ... in a folder called `.venv``) and no such path is in the project.

    The words of the part may all be in a command line (`cd artifacts && .venv\\Scripts\\python.exe ...`) while the
    thing asked for was made somewhere else, so the place is what decides. A part to delete or move a path is
    met by its absence, and is left to the other checks.
    """
    if not roots or _REMOVAL.search(item) or not (_OUTPUT_CUE.search(item) or _MAKES.search(item)):
        return False
    return any(not any((root / name).exists() for root in roots)
               for name in _named_paths(item) if _asked_to_make(item, name))


def _asked_to_make(item: str, name: str) -> bool:
    """Whether the part asks for this path to be made: the closest cue before it in its sentence is an output verb
    ("create", "called", "writes"), not an input one ("from", "reads"), or the sentence says it exists.

    Seen in a benchmark run: "It is run as `python scrape.py <address>` ... It starts at `products/page1.html` under
    that address." The page is on the shop the script reads, never in the project, so the part stayed open however
    the script was built. A path in a sentence with no output cue is one the part refers to, not one to make.
    """
    for sentence in _SENTENCE_SPLIT.split(item):
        for match in re.finditer(r"`" + re.escape(name) + r"[\\/]?`", sentence):
            before = sentence[:match.start()]
            output = [m.end() for m in _OUTPUT_CUE.finditer(before)]
            inputs = [m.end() for m in _INPUT_CUE.finditer(before)]
            if _MAKES.search(sentence) or (output and not (inputs and max(inputs) > max(output))):
                return True
    return False


def _root(word: str) -> str:
    """The start of a word that its inflections share ("remove" and "removing" -> "remo")."""
    return word[: max(4, len(word) - 2)] if len(word) >= 6 else word


# A part that asks the agent to tell the user something is done by the reply itself.
_COMMUNICATION = re.compile(
    r"^\W*(?:and\s+|also\s+)?(?:explain|tell|describe|summari[sz]e|report|say|mention|answer|state|"
    r"outline|walk|document|list)\b", re.IGNORECASE)


# A part that states how the result must behave or what it must not have, with no name to look for and no thing to
# build: "No scripts, styles or fonts from the internet", "It shows no errors in the browser console", "Diagonal tiles
# do not change", "The script must work for this shop with other products". Seen in a benchmark run: four of these
# stayed "not addressed" on pages and a scraper that passed every grader check, because the words of a property are
# not in the code that has it (a page with no internet fonts never says "internet"). Such a part is met by the files
# or by the reply; checking the property itself is the page and app checks' job.
_PART_BUILD_VERB = re.compile(
    r"\b(?:build|create|make|implement|develop|write|generate|design|code|produce|add|include|draw|save|store|put|"
    r"export|output)s?\b", re.IGNORECASE)


def _property_part(item: str) -> bool:
    # A path the part only refers to ("not only for the copy in `site/`") is not a name to look for.
    folders = [n.strip().rstrip("/\\") for n in _NAMED.findall(item) if re.fullmatch(r"[\w.-]+[\\/]", n.strip())]
    referred = {p for p in [*_named_paths(item), *folders] if not _asked_to_make(item, p)}
    names = [n for n in _NAMED.findall(item) if n.strip().rstrip("/\\") not in referred]
    return not names and not _PART_BUILD_VERB.search(item) and not _ACTION_PART.match(item)


# The words a part spends on the act of making a file, and on what kind of file it is. They are met by the file being
# there, so they cannot be looked for inside it: the code of "a file that prints ..." never says "create" or "file".
_MAKE_ACT_STEMS = frozenset(_stem(w) for w in (
    "create", "make", "write", "generate", "produce", "save", "file", "files", "script"))

# The words of a part that asks to run what was made and look at how it went. None of them names a result, so a
# command that ran the file meets the part; a part that names one ("lists twelve customers") is not made of these.
_RUN_CHECK_STEMS = frozenset(_stem(w) for w in (
    "execute", "start", "launch", "test", "tests", "try", "call", "invoke", "rerun", "check", "checks", "verify",
    "confirm", "ensure", "output", "result", "results", "work", "works", "working", "correct", "correctly",
    "expected", "right", "successfully", "succeeds", "passes",
    "script", "program", "code", "application", "module", "tool", "file", "files"))
_THE_THING_MADE = re.compile(
    r"\b(?:it|them|its|this|that|(?:the|this|that)\s+(?:script|program|code|file|files|app|application|module|tool|"
    r"result|results|output))\b", re.IGNORECASE)
# A command that only looks at a file, or prints its name, did not run it.
_LOOKS_ONLY = re.compile(
    r"^\s*(?:cat|type|more|less|head|tail|ls|dir|echo|grep|rg|findstr|sed|awk|wc|stat|file|get-content|gc|"
    r"select-string|sls|write-output|write-host)\b", re.IGNORECASE)


def _written_basenames(written: Iterable[str], roots: list[Path]) -> list[str]:
    """Lower-cased names of the files this run wrote that are there and not empty (no folder to look in: all of them)."""
    names = []
    for raw in written:
        if roots and not any(_nonempty_file(c) for c in _candidates(raw, roots)):
            continue
        name = PurePath(str(raw)).name.lower()
        if name and name not in names:
            names.append(name)
    return names


def _made_file_named(item: str, written_names: list[str]) -> bool:
    """The part asks for a file by name ("Create a file x.py that ...") and the run wrote it and left it not empty."""
    return any(PurePath(p).name.lower() in written_names for p in named_output_paths(item))


def _met_by_running_what_was_made(item: str, words: set[str], ran: Iterable[str], written_names: list[str]) -> bool:
    """A part that asks to run what the run made ("run it to check the output"), met by a command that ran one of
    the files it wrote.

    A part that is only about running and looking ("execute it and verify the result") has no word a command line
    could hold, so words were looked for there and never found. It counts when it points at what was made ("it",
    "the script", or a written file by name), every key word is one of running and looking, and a command that ran a
    written file finished without a failure. A part that names a result, or any other thing, is left to the word
    check: running the file does not show it.
    """
    if not written_names or not _ACTION_PART.match(item):
        return False
    stems = {_root(w) for w in words}
    own_names = {_root(_stem(w)) for name in written_names for w in re.findall(r"[a-z0-9_]{4,}", name)}
    allowed = {_root(s) for s in _RUN_CHECK_STEMS} | own_names
    if not stems <= allowed:
        return False
    if not (_THE_THING_MADE.search(item) or any(name in item.lower() for name in written_names)):
        return False
    for command in ran or ():
        text = str(command)
        if _LOOKS_ONLY.match(text):
            continue
        if any(re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])", text, re.IGNORECASE)
               for name in written_names):
            return True
    return False


def unaddressed_subgoals(items: Iterable[str], reply: str, written: Iterable[str],
                         evidence: str | None = None, ran: Iterable[str] = (),
                         roots: Iterable[Path] = ()) -> list[str]:
    """Items whose key words the reply and the written files never mention.

    A part counts as addressed when at least half of its key words appear in the
    reply or in a written file's name. This is deliberately loose: it exists to
    catch a part that was forgotten, and a part done but described in other
    words costs one continuation, not a failure.

    With ``evidence`` (the text of the files the run wrote, see ``deliverable_text``)
    the reply alone no longer counts for a part that asks for something to be built:
    its key words must appear in the delivered files (camelCase and snake_case are
    split, and a key word matches the start of a longer word, so "random" finds
    ``randomizeTeam`` and "remove" finds "removing"). A part that only asks the agent to tell the user something
    ("explain", "list", "summarize") is still met by the reply. This catches a reply
    that claims a part without the files showing it; it cannot tell a part built
    wrongly from one built right.

    ``ran`` (the shell commands that finished without a failure) is evidence only for a part that asks for
    something to be run, installed, started or checked, which no file can show. A part that asks for something to
    be built is never met by a command line, so an `echo` naming its words does not count.

    ``roots`` (the project folders) are where a path the part asks to make (`.venv`, `out/app.bin`) must be. When
    one is in none of them the part stays open, whatever the commands and files mention: a write the sandbox sent
    to another folder is not the thing the request asked for.
    """
    roots = [Path(r) for r in roots or ()]
    written = list(written)
    names = " ".join(PurePath(w).name for w in written)
    written_names = _written_basenames(written, roots)
    if evidence is None:
        haystack = _keywords(reply or "") | _keywords(names)
        open_items = []
        for item in items:
            words = _keywords(item)
            if words and len(words & haystack) * 2 < len(words):
                open_items.append(item)
        return open_items
    in_files = " " + _words_for_evidence(evidence + " " + names) + " "
    in_commands = " " + _words_for_evidence(" ".join(str(c) for c in ran or ())) + " "
    reply_words = _keywords(reply or "")
    open_items = []
    for item in items:
        words = _keywords(item)
        if not words:
            continue
        if _named_path_missing(item, roots):
            open_items.append(item)
            continue
        acts = bool(_ACTION_PART.match(item) or _ACTION_WORD.search(item))
        if _named_things_found(item, in_files, *((in_commands,) if acts else ())):
            continue
        if _met_by_running_what_was_made(item, words, ran, written_names):
            continue
        if _PERSIST_PART.search(item) and _STORAGE_API.search(evidence):
            continue
        # The file the part asks for is there: the words that only restate making a file are met by it.
        made = {_root(w) for w in _MAKE_ACT_STEMS} if _made_file_named(item, written_names) else set()
        if _COMMUNICATION.match(item) or _property_part(item):
            found = sum(1 for w in words if w in reply_words or (" " + _root(w)) in in_files)
        elif _ACTION_PART.match(item):
            found = sum(1 for w in words if (" " + _root(w)) in in_files or (" " + _root(w)) in in_commands)
        else:
            found = sum(1 for w in words if _root(w) in made or (" " + _root(w)) in in_files)
        if found * 2 < len(words):
            open_items.append(item)
    return open_items


# --- 4b. A request that depends on facts the model may not have ------------------------------

_BUILD_VERB = re.compile(
    r"\b(?:build|create|make|implement|develop|write|generate|design|code|produce|simulate|recreate|"
    r"replicate|clone|set\s+up)\b", re.IGNORECASE)
_STRONG_CUE = re.compile(
    r"\b(?:valid(?:\s+and\s+available)?\s+(?:in|for|on)|available\s+in|official(?:ly)?|actual|authentic|"
    r"accurate(?:ly)?|real[- ]world|faithful(?:ly)?|(?:full|complete|entire)\s+(?:roster|list|set|"
    r"catalog(?:ue)?|database|dataset|line-?up)|all\s+(?:of\s+)?the\s+(?:valid|available|official|real)|"
    r"as\s+in\s+the\s+(?:real|actual|official))\b", re.IGNORECASE)
# The user supplied the facts: a URL, a named file, an attachment, "the dataset".
_LOCAL_SOURCE = re.compile(
    r"https?://|\bwww\.|[\w./\\-]+\.(?:csv|json|txt|md|pdf|xlsx?|docx?|py|js|html|yaml|yml)\b|"
    r"\battached\b|\bprovided\b|\bgiven\s+(?:file|data|dataset|text|document)s?\b|"
    r"\bthe\s+(?:file|folder|repo|repository|codebase|dataset)\b", re.IGNORECASE)
_NAMED_SUBJECT = re.compile(r"\b([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)+)\b")
_NOT_A_SUBJECT = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january",
    "february", "march", "april", "june", "july", "august", "september", "october", "november",
    "december"})


def grounding_subject(request: str) -> str:
    """The named subject of a build request that depends on facts from outside, or "".

    True only for a request that asks for something to be built, asks for it to follow
    real rules or data ("valid and available in X", "official", "full roster"), names a
    subject (two or more capitalised words, not at the start of a sentence), and does not
    supply the facts itself (a URL, a file, an attachment). It is a signal that the model
    may be relying on facts it does not have, not proof that it is.
    """
    text = _prose(request or "")
    if len(text) < 80 or not _BUILD_VERB.search(text) or not _STRONG_CUE.search(text):
        return ""
    if _LOCAL_SOURCE.search(text):
        return ""
    for match in _NAMED_SUBJECT.finditer(text):
        phrase = match.group(1)
        before = text[:match.start()].rstrip()
        if not before or before[-1] in ".!?":
            continue
        if phrase.split()[0].lower() in _NOT_A_SUBJECT:
            continue
        return phrase
    return ""


def grounding_nudge(subject: str) -> "Nudge":
    return Nudge("grounding", continue_text(
        f"This request depends on real facts about {subject}, and you wrote files without looking anything up.",
        "Use web_search to check the rules or data you relied on, fix whatever differs, and name the sources "
        "you used in your reply. If search is unavailable, say which facts you could not check."),
        unmet=f"the request depends on facts about {subject} and nothing was looked up")


# --- 5. Which changed files deserve a "write tests" reminder ----------------------------

_SCRATCH_DIRS = frozenset({"scratch", "tmp", "temp", "sandbox-tmp", ".scratch", "experiments", "playground"})
_ENTRY_POINTS = frozenset({
    "main.py", "app.py", "run.py", "server.py", "cli.py", "manage.py", "setup.py", "conftest.py",
    "index.js", "main.js", "app.js", "server.js", "index.ts", "main.ts", "main.go", "main.rs"})
_SCRIPT_WORDS = ("scrape", "scraper", "download", "downloader", "inspect", "inspector",
                 "fetch", "crawl", "probe", "debug", "tmp", "scratch", "demo", "example")


_LIBRARY_STEM = re.compile(
    r"(?:^|[_-])(?:utils?|helpers?|lib|library|client|core|common|api|service|services|"
    r"module|base|models?|config|parser|parsers)$")


def is_library_stem(stem: str) -> bool:
    return bool(_LIBRARY_STEM.search(stem.lower()))


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
    if _LIBRARY_STEM.search(stem):
        return False   # fetch_utils.py is a library, not a throwaway fetch script
    return any(stem == w or stem.startswith(w + "_") or stem.endswith("_" + w) for w in _SCRIPT_WORDS)


_HELPER_WORDS = frozenset({"probe", "diag", "diagnose", "diagnostic", "cdp", "e2e", "inspect", "debug",
                           "tmp", "temp", "scratch", "smoke", "sandbox"})


def is_throwaway_helper(path: str, request: str, written: Iterable[str],
                        roots: Iterable[Path]) -> bool:
    """A file the run made only to look at its own work: a helper-word name, not named in
    the request, and not used by any other file the run wrote.

    A file another written file refers to by name is part of the work, whatever it is called.
    """
    p = PurePath(path)
    tokens = [t for t in re.split(r"[_\-.\s]+", p.stem.lower()) if t]
    if not tokens or not (set(tokens) & _HELPER_WORDS or any(t.startswith("diag") for t in tokens)):
        return False
    if p.name.lower() in (request or "").lower():
        return False
    roots = [Path(r) for r in roots]
    own = _file_id(path)
    for other in written:
        candidates = _candidates(str(other), roots)
        for candidate in candidates:
            if not candidate.is_file() or _file_id(candidate) == own:
                continue
            try:
                if candidate.stat().st_size > 400_000:
                    continue
                text = candidate.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if p.name.lower() in text or p.stem.lower() in text:
                return False
            break
    return True


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


# Words that say the request is about how the result looks. Used only together with a page or image the run wrote.
_VISUAL_WORDS = re.compile(
    r"\b(?:visual(?:ly)?|aesthetic\w*|polish(?:ed)?|graphics?|artwork|pixel[- ]?art|animat\w+|illustrat\w+|layout|user interface|"
    r"ui|ux|responsive|styled|styling|palette|colou?rs?|colou?rful|sprites?|beautiful|attractive|stunning|landing page|mock-?up|"
    r"looks?\s+(?:good|great|nice|professional|polished|beautiful))\b", re.IGNORECASE)


def wants_visual_result(request: str) -> bool:
    """The request is about how the result looks, not only what it does."""
    return bool(_VISUAL_WORDS.search(request or ""))


VISUAL_LOOK_ASK = (
    "What you built is meant to be seen, and nothing in this run has looked at it yet. Render it with screenshot_page (a page that "
    "needs input to reach a state can be driven with browser_test), say plainly what looks wrong or weak (layout, text that is hard "
    "to read, missing or overlapping parts, anything the request asked to be visible), and fix it. If it already looks right, say so in "
    "one line, then give your final answer.")


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


def tests_near(changed: Iterable[str]) -> tuple | None:
    """The nearest test suite above the changed files: (folder to run it from, command), or None.

    The project root the harness was started in is not always where the work lives (a run's files are
    written into its workspace), so the reminder looks upward from the files themselves. A suite beside a
    package (`proj/tests` next to `proj/pkg`) is found from any file inside the package.
    """
    for path in changed:
        try:
            # A relative path means whatever folder the process happens to be in, which says nothing about the project.
            if not Path(str(path)).is_absolute():
                continue
            folder = Path(str(path)).resolve().parent
        except (OSError, ValueError):
            continue
        for _ in range(4):
            # Never a drive root or a home folder: files lying about there belong to no project.
            if folder.parent == folder or folder == Path.home():
                break
            try:
                found = _suite_in(folder)
            except OSError:
                found = None
            if found:
                return folder, found
            folder = folder.parent
    return None


def _suite_in(folder: Path) -> str | None:
    """The command that runs the tests in this folder, or None if it holds no suite."""
    tests = [d for d in ("tests", "test") if (folder / d).is_dir()]
    files = []
    suite_dir = "."
    for d in [*tests, "."]:
        # Bound both the directory walk and the number of file samples. Do not
        # read a whole generated file merely to slice its first 4,000 characters.
        candidates = sorted(islice((folder / d).glob("test*.py" if d != "." else "test_*.py"), 128))
        if candidates and not files:
            suite_dir = d
        files.extend(p for p in candidates if p.is_file())
        files = files[:3]
        if len(files) == 3:
            break
    if files:
        text = ""
        for file in files:
            try:
                with file.open(encoding="utf-8", errors="ignore") as stream:
                    text += "\n" + stream.read(4000)
            except OSError:
                continue
        configured_pytest = (folder / "pytest.ini").is_file()
        for name, marker in (("pyproject.toml", "[tool.pytest.ini_options]"),
                             ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
            try:
                with (folder / name).open(encoding="utf-8", errors="ignore") as stream:
                    configured_pytest |= marker in stream.read(4000)
            except OSError:
                pass
        # Plain test functions need pytest even when they use no pytest imports.
        # Choose unittest only with affirmative evidence of unittest-style tests.
        if (configured_pytest or re.search(r"\bimport pytest\b|\bfrom pytest\b|^(?:async\s+)?def\s+test_", text, re.M)
                or not re.search(r"\bimport unittest\b|\bfrom unittest\b", text)):
            return "python -m pytest -q"
        return f"python -m unittest discover -s {suite_dir}"
    package = folder / "package.json"
    try:
        with package.open(encoding="utf-8") as stream:
            # Do not accept truncated or oversized metadata as valid JSON.
            raw = stream.read(65537)
        data = json.loads(raw) if len(raw) <= 65536 else None
        scripts = data.get("scripts") if isinstance(data, dict) else None
        script = scripts.get("test") if isinstance(scripts, dict) else None
    except (OSError, ValueError):
        script = None
    if isinstance(script, str) and script.strip():
        return "npm test"
    return None


def verify_ask(changed: Iterable[str], has_tests: bool) -> str:
    """The verification reminder with the files and the exact command named.

    Small models do the check when told precisely what to run; a bare "inspect
    or test the result" is often skipped or answered with an unrelated command.
    """
    changed = list(changed)
    names = []
    for path in changed:
        name = PurePath(str(path)).name
        if name and name not in names:
            names.append(name)
    if not names:
        return VERIFY_ASK
    shown = ", ".join(names[:3]) + (f" (+{len(names) - 3} more)" if len(names) > 3 else "")
    first = names[0]
    suffix = PurePath(first).suffix.lower()
    near = None if has_tests else tests_near(changed)
    if has_tests:
        command = "run_tests"
    elif near:
        # The project's own tests, run from the folder they belong to: running a package's module files one by
        # one as scripts fails on their relative imports, which is a problem with the check, not with the code.
        command = f"{near[1]}` from `{near[0]}"
    elif suffix == ".py" and _package_of(changed[0]):
        package, parent = _package_of(changed[0])
        command = f"python -c \"import {package}.{PurePath(first).stem}\"` from `{parent}"
    elif suffix == ".py":
        command = f"python {first}"
    elif suffix in {".js", ".mjs", ".cjs"}:
        command = f"node {first}"
    elif suffix in {".html", ".htm"}:
        command = "check_app on the page"
    elif suffix in {".sh", ".bash"}:
        command = f"bash {first}"
    else:
        command = f"read_text on {first}"
    return (f"Continue, and also run what you changed ({shown}) with `{command}` now, "
            "fix anything that fails, then reply. If it cannot be run here, say so plainly.")


def _package_of(path: str) -> tuple | None:
    """(dotted package name, folder that holds it) for a file inside a Python package, else None."""
    try:
        if not Path(str(path)).is_absolute():
            return None
        folder = Path(str(path)).resolve().parent
    except (OSError, ValueError):
        return None
    if not (folder / "__init__.py").is_file():
        return None
    parts = [folder.name]
    while (folder.parent / "__init__.py").is_file() and folder.parent != folder:
        folder = folder.parent
        parts.insert(0, folder.name)
    return ".".join(parts), folder.parent


def outputs_nudge(missing: list[str]) -> Nudge:
    listed = ", ".join(missing[:6])
    return Nudge("outputs", continue_text(
        f"These files were required but are missing or empty: {listed}.",
        "Continue: create them now, or say plainly why they cannot be created."),
        unmet=f"missing or empty files: {listed}")


def partial_files_nudge(paths: list[str]) -> Nudge:
    listed = ", ".join(paths[:4])
    return Nudge("partial_file", continue_text(
        f"{listed} was cut off while being written and holds only the part that was saved.",
        'Continue: add the rest with write_file mode="append", then check the whole file.'),
        unmet=f"a file was cut off and not completed: {listed}")


def subgoals_nudge(items: list[str], evidence: bool = False) -> Nudge:
    listed = "; ".join(f"({n}) {item}" for n, item in enumerate(items[:6], 1))
    if evidence:
        return Nudge("subgoals", continue_text(
            f"These parts of the request do not show up in anything you wrote: {listed}.",
            "Continue with those parts, or point to the file and function that already does each one. "
            "If a part cannot be done, say so in your reply."),
            unmet="parts of the request not found in the delivered files: " + "; ".join(items[:6]))
    return Nudge("subgoals", continue_text(
        f"Some parts of the request have not been addressed yet: {listed}.",
        "Continue with those parts. When a part is done, or cannot be done, say so in your reply."),
        unmet="parts of the request not addressed: " + "; ".join(items[:6]))


_SUCCESS_CLAIM = re.compile(
    r"\b(?:all|every)\s+(?:checks?|tests?)\s+(?:pass|passed|passing)|everything\s+(?:works|is working|passes)|"
    r"fully\s+(?:working|functional|implemented)|successfully\s+(?:completed|implemented|built)|"
    r"\ball\s+done\b|\bis\s+(?:now\s+)?complete\b|\bwork(?:s|ing)\s+as\s+(?:expected|intended)\b", re.IGNORECASE)


def claims_success(reply: str) -> bool:
    return bool(_SUCCESS_CLAIM.search(_prose(reply or "")))


def failing_check_nudge(detail: str, reply: str = "") -> Nudge:
    lead = ("Your reply says the work is done, but the latest automated check failed and has not passed since: "
            if claims_success(reply) else
            "The latest automated check failed and has not passed since: ")
    return Nudge("failing_check", continue_text(
        f"{lead}{detail}.",
        "Fix the cause and run the check again. If it cannot be fixed, say plainly in your reply that it still "
        "fails and why, instead of reporting the work as done."),
        unmet="the latest check failed: " + detail[:120])


def tests_nudge(paths: list[str]) -> Nudge:
    return Nudge("tests", continue_text(
        "This project has a test suite, and these library files changed without tests: "
        + ", ".join(paths[:6]) + ".",
        "Continue: call generate_tests on each, or say why tests do not apply."),
        unmet="library files changed without tests: " + ", ".join(paths[:6]))

