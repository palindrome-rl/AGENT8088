"""Small, session-scoped operational state for the shared agent loop."""

from __future__ import annotations

import re
import shlex
from copy import deepcopy
from pathlib import PurePath

from agent8088.completion import SUBGOAL_PREFIX

VERSION = 2
MAX_OPERATIONS = 12
MAX_TEXT = 240
MAX_EVIDENCE = 6


MAX_WRITTEN = 50
MAX_UNTESTED = 8
MAX_FAILING_NUDGES = 2   # follow-ups per request about a check that is still failing
MAX_VERIFICATION_REQUESTS = 2
# Tools that look something up outside the workspace.
RESEARCH_TOOLS = frozenset({"web_search", "browse_page"})

# Suffixes worth a test. Config, data, docs and markup are excluded because a
# generated test for them asserts that a file parses, which nobody needed.
CODE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".java",
    ".kt", ".cs", ".php", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp",
})

# Throwaway scripts. A scratch file is not a deliverable, so it never prompts a
# run or a test.
SCRATCH_DIRS = frozenset({"scratch", "tmp", "temp", ".scratch"})
SCRATCH_PREFIXES = ("scrape_", "inspect_", "debug_", "download_", "check_",
                    "probe_", "tmp_", "test_run_")
# A shell run that printed one of these did not succeed, so it proves nothing.
_RUN_FAILED_RE = re.compile(r"^Error:|Command exited with status [1-9]\d*|timed out after \d+s")


def _is_test_path(path: str) -> bool:
    p = PurePath(path)
    stem = p.stem.lower()
    return (stem.startswith("test_") or stem.endswith("_test")
            or ".test" in p.name.lower() or ".spec" in p.name.lower()
            or any(part.lower() in {"tests", "test", "__tests__", "spec"}
                   for part in p.parts))


def _is_code_path(path: str) -> bool:
    return PurePath(path).suffix.lower() in CODE_SUFFIXES


_LIBRARY_STEM = re.compile(
    r"(?:^|[_-])(?:utils?|helpers?|lib|library|client|core|common|api|service|services|"
    r"module|base|models?|config|parser|parsers)$")


def _is_scratch_path(path: str, workspace: str = "") -> bool:
    p = PurePath(path)
    if workspace:
        # A project under a folder named 'temp' is not scratch; only the part
        # inside the workspace is judged.
        try:
            p = p.relative_to(workspace)
        except ValueError:
            pass
    if _LIBRARY_STEM.search(p.stem.lower()):
        return False
    return (any(part.lower() in SCRATCH_DIRS for part in p.parts[:-1])
            or p.name.lower().startswith(SCRATCH_PREFIXES))


def _names_run(command: str, path: str) -> bool:
    """Whether a shell command mentions a file by its name or its module."""
    tokens = {PurePath(path).name, _module_key(path)} - {""}
    return any(re.search(rf"(?<![\w.-]){re.escape(token)}(?![\w.-])", command, re.IGNORECASE)
               for token in tokens)


def _runs_script(command: str, path: str, workspace: str = "") -> bool:
    """Conservative evidence of an actual script invocation, not an echo/read/compile.

    Compound shell commands are ambiguous (a later success can hide an earlier
    failure), so they leave the honest unverified note in place.
    """
    import posixpath
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace_split, lexer.commenters, lexer.escape = True, "", ""
        args = list(lexer)
    except ValueError:
        return False
    if len(args) < 2 or any(a and all(c in ";&|()\n" for c in a) for a in args):
        return False
    program = args.pop(0).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?|node(?:\.exe)?", program):
        return False
    while args and args[0] in {"-u", "-B", "-E", "-s", "-S"}:
        args.pop(0)
    if not args or args[0].startswith("-") or any(a in {"-h", "--help", "--version"} for a in args[1:]):
        return False
    script = args[0].replace("\\", "/")
    if not posixpath.isabs(script) and not re.match(r"^[A-Za-z]:/", script):
        if workspace:
            script = posixpath.join(workspace.replace("\\", "/"), script)
        else:
            # Older saved trajectories have no workspace. Only a bare filename
            # can be compared there; explicit different directories cannot.
            return script == PurePath(path).name
    return _file_key(script) == _file_key(path)


def _module_key(path: str) -> str:
    """The stem a test file and its source share.

    'src/parser.py' and 'tests/test_parser.py' both reduce to 'parser', which is
    how a written test clears the source it covers without needing the test to
    declare what it tests.
    """
    stem = PurePath(path).stem.lower()
    for prefix in ("test_", "test-"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
    for suffix in ("_test", "-test", ".test", ".spec"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def _text(value) -> str:
    return " ".join(str(value or "").split())[:MAX_TEXT]


# A shell command that runs something to find out whether it works. A command that only looks around
# (a listing, a copy, a probe) and exits non-zero did not fail a check, so it must not leave the
# run marked "the latest automated check failed" for good.
_CHECK_COMMAND = re.compile(
    r"(?<![a-z])(?:test|tests|spec|specs|pytest|unittest|jest|mocha|vitest|playwright|cypress|check|checks|"
    r"verify|validate|lint|ruff|flake8|pylint|mypy|pyright|eslint|tsc|build|compile|py_compile|compileall|"
    r"make|cmake|cargo|e2e|smoke)(?![a-z])", re.IGNORECASE)


# The note the sandbox appends when a command tried to write where it may not. A check that ends with
# it was stopped by the sandbox: it never ran to a result, so it did not fail.
_SANDBOX_BLOCKED = re.compile(r"\[sandbox\] Shell commands may write only")


# Commands that move, list or remove files. A folder called `tests\` in their arguments does not make them a
# check (`xcopy proj\tests dest` failing is a copy failing), so a segment that starts with one is not read for
# check words. Every other command keeps the plain word match, so an unfamiliar check is still a check.
_FILE_COMMANDS = frozenset({
    "xcopy", "copy", "robocopy", "move", "del", "erase", "rmdir", "rd", "mkdir", "md", "ren", "rename", "dir",
    "type", "attrib", "tree", "cd", "chdir", "pushd", "popd", "echo", "cls", "set", "ls", "cp", "mv", "rm",
    "cat", "touch", "pwd", "mkdir", "chmod", "ln", "find", "stat", "where", "which",
    "copy-item", "move-item", "remove-item", "get-childitem", "new-item", "set-location", "get-content"})
def command_segments(command: str, *, split_or: bool = False) -> list[str]:
    """Split shell sequencing outside quotes, without splitting `2>&1`.

    Shared by runner evidence and failed-check bookkeeping so Windows `&`
    sequences and quoted labels are classified consistently.
    """
    pieces, current, quote, i = [], [], "", 0
    text = command or ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            current.append(ch)
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif (ch in ";\n" or (ch == "&" and not (current and current[-1] in "<>|"))
              or (split_or and text[i:i + 2] == "||")):
            pieces.append("".join(current))
            current = []
            if text[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            current.append(ch)
        i += 1
    pieces.append("".join(current))
    return [piece for piece in pieces if piece.strip()]


# Asking a tool for its version or help text says nothing about the work, whichever way it exits: a
# `pytest --version` that fails only means pytest is not installed, and one that works proves nothing.
# Short flags are case-sensitive: pytest -v runs tests verbosely, while -V prints its version.
_PROBE_FLAG = re.compile(r"(?:^|\s)(?:--version|-V|--help|-h)\s*$")
_TRAILING_REDIRECT = re.compile(r"\s*(?:\d?>>?&?\S*)\s*$")
_SETUP_PROGRAMS = frozenset({"cd", "chdir", "pushd", "echo"})


def _is_probe(command: str) -> bool:
    """Whether every real step of the command only asks a tool for its version or help text."""
    steps = [segment for segment in command_segments(command or "", split_or=True)
             if _segment_program(segment) not in _SETUP_PROGRAMS]
    if not steps:
        return False
    for step in steps:
        text = step.strip()
        while True:
            stripped = _TRAILING_REDIRECT.sub("", text)
            if stripped == text:
                break
            text = stripped
        if not _PROBE_FLAG.search(text):
            return False
    return True


def _segment_program(segment: str) -> str:
    words = segment.strip().split()
    while words and (words[0].lower() in ("call", "start", "cmd", "/c") or re.match(r"^[A-Za-z_]\w*=", words[0])):
        words = words[1:]
    return PurePath(words[0].strip("\"'")).name.lower().removesuffix(".exe") if words else ""


def _is_check_run(command: str) -> bool:
    """Whether a shell command is a check: no command at all means a dedicated check tool, which is."""
    if not (command or "").strip():
        return True
    return any(_CHECK_COMMAND.search(segment) for segment in command_segments(command, split_or=True)
               if segment.strip() and _segment_program(segment) not in _FILE_COMMANDS)


# Running the program the run itself wrote is the plainest way to see whether it works, whatever the
# command is called: `python main.py` crashing is a failed check, `type main.py` failing is not.
_RUNNER = (r"(?:python[\d.]*|py|node|nodejs|deno|bun|ruby|php|perl|bash|sh|pwsh|powershell|java|dotnet|"
           r"go\s+run|cargo\s+run|ts-node|tsx)")


def _runs_a_written_file(command: str, written) -> bool:
    for path in written or []:
        name = PurePath(str(path)).name
        if name and re.search(rf"(?<![\w.-]){_RUNNER}(?![\w-])[^&|;\n]*?(?<![\w.-]){re.escape(name)}(?![\w-])",
                              command or "", re.IGNORECASE):
            return True
    return False


# What a script says when it was run in the wrong place: a browser file started under Node or Python (no `document`, no
# `window`), or an ES module started as a plain script. The file may be fine where it belongs, so that run says
# nothing about it. Only a run of a file the run wrote, with no check word in the command, is read this way: a
# failing test or check script is still a failure.
_WRONG_PLACE = re.compile(
    r"\b(?:document|window|navigator|localStorage|sessionStorage|alert|confirm|Image|Audio|HTMLElement|"
    r"requestAnimationFrame|XMLHttpRequest|customElements|MutationObserver|ResizeObserver|IntersectionObserver|"
    r"AudioContext|CanvasRenderingContext2D)\s+is not defined"
    r"|Cannot use import statement outside a module|Unexpected token 'export'"
    r"|require is not defined in ES module scope|ERR_REQUIRE_ESM|ERR_UNKNOWN_FILE_EXTENSION"
    r"|name '(?:document|window|navigator)' is not defined", re.IGNORECASE)
_TRACEBACK = re.compile(r"Traceback \(most recent call last\)|^\s*(?:\w+\.)*\w*(?:Error|Exception)\b", re.MULTILINE)
# `cd "C:\long\path" &&` in front of the command says where, not what.
_CD_PREFIX = re.compile(r"""^\s*(?:cd|chdir|pushd)\s+(?:/d\s+)?(?:"[^"]*"|'[^']*'|\S+)\s*(?:&&|;|&)\s*""", re.IGNORECASE)


def _command_core(command: str) -> str:
    """The command without the `cd <folder> &&` in front of it (up to two of them)."""
    text = (command or "").strip()
    for _ in range(2):
        stripped = _CD_PREFIX.sub("", text, count=1)
        if stripped == text or not stripped.strip():
            break
        text = stripped
    return text


def _wrong_place_failure(command: str, result: str, written) -> bool:
    """A run of a written file that failed only because it was started where it does not belong."""
    return (bool(_WRONG_PLACE.search(str(result)))
            and _runs_a_written_file(command, written)
            and not _is_check_run(command))


# A lone `&` (cmd.exe's "then run this too", not `&&`, `2>&1` or `>&`) outside quotes.
_LONE_AMPERSAND = re.compile(r"(?<![&>])&(?![&>0-9])")
_DIAGNOSTIC_PROGRAMS = frozenset({"dir", "ls", "type", "cat", "echo", "pwd", "where", "which",
                                  "get-childitem", "get-content"})


def _without_trailing_clause(text: str) -> str:
    """Drop only a simple diagnostic trailer, never another check or arbitrary executable."""
    for match in _LONE_AMPERSAND.finditer(text):
        before = text[:match.start()]
        if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
            tail = text[match.end():].strip()
            # Be conservative about pipelines, further sequencing, substitutions and escapes:
            # passing the first command cannot prove that any later real work succeeded.
            if (_segment_program(tail) in _DIAGNOSTIC_PROGRAMS
                    and not re.search(r"[&|;\r\n`^$%!()]", tail)):
                return before.strip()
            return text
    return text


def _same_command(a: str, b: str) -> bool:
    """Whether `b` is the command `a` that failed, run again.

    `b` may also be `a` without a clause tacked on after a lone `&`: in cmd.exe the exit status of
    `check & dir x` is that of `dir x`, so the check can look failed while it passed. The other way round
    does not count: `check & echo done` says nothing about `check`."""
    if not a:
        return False
    first, second = " ".join(str(a).split()).lower(), " ".join(str(b).split()).lower()
    return first == second or (bool(second) and _without_trailing_clause(first) == second)


def _file_key(path: str) -> str:
    import os
    import posixpath
    key = posixpath.normpath(str(path).replace("\\", "/"))
    return key.lower() if os.name == "nt" or (len(key) > 1 and key[1] == ":") else key


def _blank(goal: str = "", workspace: str = "") -> dict:
    return {
        "version": VERSION,
        "goal": _text(goal),
        "workspace": _text(workspace),
        "operations": [],
        "checklist": [
            {"name": "Inspect the current state", "status": "pending"},
            {"name": "Complete the requested work", "status": "pending"},
            {"name": "Verify changed work", "status": "not_needed"},
        ],
        "evidence": [],
        "mutation_count": 0,
        "verification": "not_needed",
        "verification_requested": False,
        "verification_requests": 0,
        "untested_changes": [],
        "written_paths": [],
        "tests_requested": False,
        "passed_checks": 0,
        "check_failing": False,
        "failing_check": "",
        "failing_nudges": 0,
        "subgoals_built": False,
        "unmet_checks": [],
        "partial_files": [],
        "research_used": False,
        "grounding_asked": False,
        "smoke_notes": [],
        "smoke_failures": 0,
        "smoke_checked": {},
        "smoke_passed_at": {},
        "smoke_unverified": {},
        "weak_secret_asked": False,
        "smoke_asked": [],
        "consecutive_setbacks": 0,
        "stalled": False,
        "replan_requested": False,
        "message_count": 0,
    }


class TrajectoryState:
    """Mutates one JSON-serializable session dictionary in place.

    It records only controller-observed facts. Full arguments and tool output
    remain in the transcript, avoiding another secret-bearing copy of them.
    """

    def __init__(self, state: dict | None, goal: str = "", message_count: int = 0,
                 workspace: str = ""):
        self.data = state if isinstance(state, dict) else {}
        self._normalise(goal, message_count, workspace)
        # Files present when this request started (path -> size, mtime). Kept on the
        # object, not in the saved state: it describes this call only.
        self.start_files: dict | None = None

    def _normalise(self, goal: str, message_count: int, workspace: str) -> None:
        if not isinstance(self.data.get("operations"), list):
            self.data.clear()
            self.data.update(_blank(goal, workspace))
        elif self.data.get("version") not in {1, VERSION}:
            self.data.clear()
            self.data.update(_blank(goal, workspace))
        elif self.data.get("version") == 1:
            upgraded = _blank(self.data.get("goal", goal), workspace)
            upgraded.update(self.data)
            upgraded["version"] = VERSION
            self.data.clear()
            self.data.update(upgraded)
        for key, value in _blank().items():
            self.data.setdefault(key, deepcopy(value))
        if workspace:
            self.data["workspace"] = _text(workspace)
        previous_count = int(self.data.get("message_count") or 0)
        unresolved = (self.data.get("verification") == "pending" or any(
            op.get("status") in {"running", "blocked", "failed", "unknown"}
            for op in self.data["operations"]
        ))
        if goal and (not self.data.get("goal") or (message_count > previous_count and not unresolved)):
            self.data.clear()
            self.data.update(_blank(goal, workspace))
        elif message_count > previous_count:
            # Unresolved state carries over to the next request; the reminder
            # budget is per request and must not, or the session goes silent.
            self.data["verification_requests"] = 0
            self.data["unmet_checks"] = []
            self.data["smoke_failures"] = 0
            self.data["failing_nudges"] = 0
            # The old request's parts and smoke notes describe work the person
            # has moved on from: carrying them over nudges the new request about
            # the old one and stamps stale notes on its answer.
            self.data["subgoals_built"] = False
            self.data["checklist"] = [step for step in self.data.get("checklist", [])
                                      if not str(step.get("name", "")).startswith(SUBGOAL_PREFIX)]
            self.data["smoke_notes"] = []
            self.data["smoke_checked"] = {}
            self.data["smoke_passed_at"] = {}
            self.data["smoke_output"] = {}
            self.data["smoke_unverified"] = {}
            self.data["weak_secret_asked"] = False
            self.data["smoke_asked"] = []
            self.data["grounding_asked"] = False
        self.data["message_count"] = message_count
        for operation in self.data["operations"]:
            if operation.get("status") == "running":
                operation["status"] = "unknown"

    def before_tool(self, name: str) -> dict:
        operation = {"tool": _text(name), "status": "running"}
        self.data["operations"].append(operation)
        del self.data["operations"][:-MAX_OPERATIONS]
        return operation

    def after_tool(self, operation: dict, result: str, *, failed: bool,
                   blocked: bool, mutated: bool, verdict: str = "unknown",
                   path: str = "", command: str = "", existed: bool = False,
                   partial_path: str = "", written_to: str = "") -> None:
        operation["status"] = "blocked" if blocked else "failed" if failed else "done"
        if operation["status"] == "done" and (
                operation["tool"] in RESEARCH_TOOLS
                or (operation["tool"] == "execute_shell" and re.search(r"https?://", command or ""))):
            self.data["research_used"] = True
        operation["result"] = _text(result)
        operation["verdict"] = verdict if verdict in {"passed", "failed"} else "unknown"
        if operation["status"] == "done":
            self._set_step("Inspect the current state", "done")
            self.data["consecutive_setbacks"] = 0
            self.data["stalled"] = False
        else:
            self.data["consecutive_setbacks"] = int(self.data.get("consecutive_setbacks") or 0) + 1
        if mutated and path and operation["status"] == "done" and _is_code_path(path):
            changed = self.data.setdefault("changed_code", [])
            if path not in changed:
                changed.append(path)
                del changed[:-MAX_UNTESTED]
        if (mutated and path and operation["status"] == "done"
                and operation.get("tool") == "write_file" and self.data.get("partial_files")):
            # A later write to a file saved part-way is the continuation it was waiting for.
            name = _file_key(partial_path or path)
            self.data["partial_files"] = [p for p in self.data["partial_files"]
                                          if p.get("key", _file_key(p.get("path", ""))) != name]
        if mutated and path and operation["status"] == "done":
            # Where the work actually landed. The answer quotes this, because a
            # run whose writes were redirected into the sandbox workspace reads
            # as a false completion claim when the user checks the folder they
            # named and finds it empty.
            written = self.data.setdefault("written_paths", [])
            if path not in written:
                written.append(path)
                del written[:-MAX_WRITTEN]
            if written_to and written_to != path:
                # The path the agent asked for is not where the file is. Keep both,
                # so the end of the run can put a copy back where the user named.
                landed = self.data.setdefault("redirected_writes", [])
                if [path, written_to] not in landed:
                    landed.append([path, written_to])
                    del landed[:-MAX_WRITTEN]
        # Changing a file is not evidence that the failed check is fixed.
        # Keep the failure visible until a subsequent check actually passes,
        # including failures from checks that also modify the workspace.
        if operation["status"] == "done" and operation["verdict"] != "failed" and command:
            self._clear_unverified_run(command)
        written_now = self.data.get("written_paths")
        if (operation["verdict"] == "failed"
                and (_is_check_run(command) or _runs_a_written_file(command, written_now))
                and not _is_probe(command)
                and not _SANDBOX_BLOCKED.search(str(result))
                and not _wrong_place_failure(command, result, written_now)):
            self.data["check_failing"] = True
            what = (_command_core(command) or operation["tool"]).strip().replace("\n", " ")[:100]
            self.data["failing_check"] = _text(f"{what} -> {operation['result']}")[:200]
            self.data["failing_command"] = " ".join(command.split())
            # The written files this run started: a clean run of any of them later settles the failure.
            self.data["failing_files"] = ([] if _is_check_run(command) else
                                         [PurePath(str(f)).name for f in written_now or []
                                          if _runs_a_written_file(command, [f])][:6])
            # Display notes are deliberately shortened. Keep the check target
            # separately so a long path cannot prevent its successful retry
            # from settling the failure.
            target = (re.match(r"^(.+?): smoke test FAILED\b", str(result or ""))
                      if operation["tool"] == "check_app" else None)
            self.data["failing_check_app_path"] = (path or target.group(1)) if target else ""
        elif operation["verdict"] == "passed":
            self.data["check_failing"] = False
            self.data["failing_check"] = ""
        elif (operation["status"] == "done" and operation["verdict"] != "failed" and command
                and self.data.get("check_failing")
                and not _TRACEBACK.search(str(result))
                and _same_command(self.data.get("failing_command"), command)):
            # The very command that failed has now run without failing.
            self.data["check_failing"] = False
            self.data["failing_check"] = ""
        elif (operation["status"] == "done" and operation["verdict"] != "failed" and command
                and self.data.get("check_failing") and self.data.get("failing_files")
                and not _TRACEBACK.search(str(result))
                and any(_runs_a_written_file(command, [name]) for name in self.data["failing_files"])):
            # The failure was a run of a file the run wrote; that file has now run again, exited cleanly and
            # printed no error, whatever else the command line said.
            self.data["check_failing"] = False
            self.data["failing_check"] = ""
            self.data["failing_files"] = []
        if mutated:
            self.data["mutation_count"] = int(self.data.get("mutation_count") or 0) + 1
            self._set_step("Complete the requested work", "done")
            scratch = bool(path) and _is_scratch_path(path, self.data.get("workspace", ""))
            if not scratch:
                self.data["verification"] = "pending"
                self.data["verification_requested"] = False
                self._set_step("Verify changed work", "pending")
        if operation["verdict"] == "passed":
            self.data["passed_checks"] = int(self.data.get("passed_checks") or 0) + 1
            self.data["evidence"].append({"tool": operation["tool"], "result": operation["result"]})
            del self.data["evidence"][:-MAX_EVIDENCE]
            if self.needs_verification():
                self.data["verification"] = "verified"
                self._set_step("Verify changed work", "done")
        elif (self.needs_verification() and operation["status"] == "done"
              and not mutated and operation["tool"] in {"read_text", "execute_shell", "git_diff"}
              and self._touches_changed_work(operation["tool"], path, command)):
            self.data["verification"] = "inspected"
            self._set_step("Verify changed work", "inspected")
            self.data["evidence"].append({"tool": operation["tool"], "result": operation["result"]})
            del self.data["evidence"][:-MAX_EVIDENCE]
        if mutated and path and operation["status"] == "done":
            self._note_test_edit(path, existed)
        if self.data["consecutive_setbacks"] >= 2:
            self.data["stalled"] = True
        self._track_tests(operation, path, mutated=mutated, command=command)

    def _touches_changed_work(self, tool: str, path: str, command: str) -> bool:
        """Is this read/run about a file this run changed? An unrelated `ls` or
        read is not a check. With no recorded writes (changes made through the
        shell) there is nothing to match against, so any inspection counts."""
        if tool == "git_diff":
            return True
        written = self.data.get("written_paths") or []
        if not written:
            return True
        for changed in written:
            if tool == "read_text" and path and PurePath(path).name == PurePath(changed).name:
                return True
            if tool == "execute_shell" and command and _names_run(command, changed):
                return True
        return False

    def _note_test_edit(self, path: str, existed: bool) -> None:
        """Remember test files that already existed and were changed: the usual
        way a weak model makes a failing suite pass is to edit the test."""
        created = self.data.setdefault("created_files", [])
        if not existed:
            if path not in created:
                created.append(path)
                del created[:-MAX_WRITTEN]
            return
        if _is_test_path(path) and not self._created_here(path):
            edited = self.data.setdefault("edited_existing_tests", [])
            if path not in edited:
                edited.append(path)
                del edited[:-MAX_WRITTEN]

    def _created_here(self, path: str) -> bool:
        """Whether this run created `path`, under the name it was asked for or where it landed.

        A file the sandbox diverted is recorded when it is created under the path that was asked for,
        and a later edit may name where it landed; the two are one file.
        """
        keys = {_file_key(p) for p in self.data.get("created_files") or []}
        if _file_key(path) in keys:
            return True
        for requested, landed in self.redirected_writes():
            pair = {_file_key(requested), _file_key(landed)}
            if _file_key(path) in pair and pair & keys:
                return True
        return False

    def edited_existing_tests(self) -> list[str]:
        return list(self.data.get("edited_existing_tests") or [])

    def _track_tests(self, operation: dict, path: str, *, mutated: bool, command: str = "") -> None:
        """Maintain the list of code files changed this run with no test written.

        Only successful writes count. A blocked or failed write changed nothing,
        and nudging about it would send the model to write tests for code that
        was never saved.
        """
        if operation.get("status") != "done":
            return
        untested = self.data["untested_changes"]
        tool = operation.get("tool", "")

        if tool == "run_tests" and operation.get("verdict") == "passed":
            untested.clear()
            self.data["tests_requested"] = False
            return

        if tool == "execute_shell":
            # Running the file this run changed is its check, like a passing
            # run_tests, but only when the run actually succeeded.
            if not _RUN_FAILED_RE.search(operation.get("result", "")):
                untested[:] = [p for p in untested if not _names_run(command, p)]
            return

        if not path:
            return

        if tool == "generate_tests":
            key = _module_key(path)
            untested[:] = [p for p in untested if _module_key(p) != key]
            return

        if not mutated or not _is_code_path(path):
            return

        if _is_test_path(path):
            key = _module_key(path)
            untested[:] = [p for p in untested if _module_key(p) != key]
            return

        if _is_scratch_path(path, self.data.get("workspace", "")):
            return

        if path not in untested:
            untested.append(path)
            del untested[:-MAX_UNTESTED]
            # A new untested change re-arms the one-shot: the previous nudge was
            # about different files.
            self.data["tests_requested"] = False

    def mutation_count(self) -> int:
        return int(self.data.get("mutation_count") or 0)

    def written_paths(self) -> list[str]:
        """Paths this run wrote successfully, first write first."""
        return list(self.data.get("written_paths") or [])

    def landed_paths(self) -> list[str]:
        """The files this run wrote, each at the place it actually is.

        A write the sandbox diverted is recorded under the path that was asked for, but the file
        is where it landed. Checks that open the files (the smoke test, the credential scan) need
        that place, or they find nothing there and silently skip the file.
        """
        moved = {_file_key(req): landed for req, landed in self.redirected_writes()}
        paths: list[str] = []
        for path in self.written_paths():
            actual = moved.get(_file_key(path), path)
            if actual not in paths:
                paths.append(actual)
        return paths

    def redirected_writes(self) -> list[tuple[str, str]]:
        """(requested, landed) pairs for writes the sandbox routed elsewhere."""
        return [(req, landed) for req, landed in self.data.get("redirected_writes") or []]

    def changed_code_paths(self) -> list[str]:
        """Code files this run wrote, newest last."""
        return list(self.data.get("changed_code") or [])

    def untested_paths(self) -> list[str]:
        return list(self.data.get("untested_changes") or [])

    def needs_tests(self) -> bool:
        return bool(self.data.get("untested_changes"))

    def request_tests(self, relevant=None) -> bool:
        """Arm the one-shot tests reminder, for the changed files `relevant` accepts.

        `relevant(path) -> bool` narrows the reminder to files worth a unit test;
        with none left it does not fire and is not spent.
        """
        if not self.needs_tests() or self.data.get("tests_requested"):
            return False
        if relevant is not None and not self.relevant_untested(relevant):
            return False
        self.data["tests_requested"] = True
        return True

    def relevant_untested(self, relevant) -> list[str]:
        return [p for p in self.untested_paths() if relevant(p)]

    # --- completion checks -------------------------------------------------

    def set_subgoals(self, items: list[str]) -> None:
        """Record the parts of a multi-part request, once per request."""
        if self.data.get("subgoals_built"):
            return
        self.data["subgoals_built"] = True
        checklist = self.data.setdefault("checklist", [])
        for item in items:
            checklist.append({"name": SUBGOAL_PREFIX + _text(item), "status": "pending"})

    def subgoals_built(self) -> bool:
        return bool(self.data.get("subgoals_built"))

    def subgoals(self) -> list[dict]:
        return [step for step in self.data.get("checklist", [])
                if str(step.get("name", "")).startswith(SUBGOAL_PREFIX)]

    def open_subgoals(self) -> list[str]:
        return [s["name"][len(SUBGOAL_PREFIX):] for s in self.subgoals() if s.get("status") != "done"]

    def mark_subgoals(self, still_open: list[str]) -> None:
        """Everything not in `still_open` is done; what is in it stays pending."""
        keep = set(still_open)
        for step in self.subgoals():
            name = step["name"][len(SUBGOAL_PREFIX):]
            step["status"] = "pending" if name in keep else "done"

    def unresolved_tools(self) -> list[str]:
        latest = {}
        for operation in self.data.get("operations", []):
            latest[operation.get("tool", "tool")] = operation
        return [tool for tool, op in latest.items()
                if op.get("status") in {"blocked", "failed", "unknown"}]

    def has_open_items(self) -> bool:
        """Work the run itself shows is not finished: pending verification, a failed
        or blocked tool, or a part of the request nobody has addressed."""
        return bool(self.needs_verification() or self.unresolved_tools() or self.open_subgoals()
                    or self.partial_files())

    def note_partial(self, path: str, size: int) -> None:
        """A file whose write was cut off and saved only up to its last whole line."""
        files = [p for p in self.data.setdefault("partial_files", [])
                 if p.get("key", _file_key(p.get("path", ""))) != _file_key(path)]
        files.append({"path": _text(path), "key": _file_key(path), "bytes": int(size)})
        self.data["partial_files"] = files[-6:]

    def partial_files(self) -> list[str]:
        return [str(p.get("path", "")) for p in self.data.get("partial_files") or []]

    def salvaged_before(self, path: str) -> bool:
        """Was a cut-off write to this file already saved once this run?"""
        return _file_key(path) in (self.data.get("salvaged_once") or [])

    def mark_salvaged(self, path: str) -> None:
        done = self.data.setdefault("salvaged_once", [])
        if _file_key(path) not in done:
            done.append(_file_key(path))
            del done[:-20]

    def progress_mark(self) -> tuple:
        """A value that changes when the run makes real progress."""
        return (int(self.data.get("mutation_count") or 0),
                int(self.data.get("passed_checks") or 0),
                sum(1 for step in self.subgoals() if step.get("status") == "done"))

    def smoke_unchecked(self, paths: list[str]) -> list[str]:
        """Deliverables not yet smoke-tested at the current state of the work."""
        checked = self.data.setdefault("smoke_checked", {})
        return [p for p in paths if checked.get(p) != self.mutation_count()]

    def record_smoke(self, path: str, summary: str, failed: bool, output: str = "",
                     passed: bool = False, unverified: bool = False) -> None:
        self.data.setdefault("smoke_checked", {})[path] = self.mutation_count()
        passed_at = self.data.setdefault("smoke_passed_at", {})
        if passed and not failed and not unverified:
            passed_at[path] = self.mutation_count()
        else:
            passed_at.pop(path, None)
        if output:
            # Raw start-up output of a failed script check, for diagnosis. Kept out of
            # the notes the model and the person read; bounded to the last few files.
            kept = self.data.setdefault("smoke_output", {})
            # _text is for one-line notes and cuts at 240 characters. This is
            # already clipped diagnostic output: preserve its traceback tail.
            kept[path] = str(output)[:900]
            for old in list(kept)[:-4]:
                del kept[old]
        notes = self.data.setdefault("smoke_notes", [])
        notes[:] = [n for n in notes if not n.startswith(summary.split(":", 1)[0] + ":")]
        notes.append(_text(summary))
        del notes[:-4]
        if failed:
            self.data["smoke_failures"] = int(self.data.get("smoke_failures") or 0) + 1
        pending = self.data.setdefault("smoke_unverified", {})
        if unverified:
            pending[path] = summary.split(":", 1)[0]
        else:
            pending.pop(path, None)
        if passed:
            # The page or script passed on the latest state of the work, so an earlier failing
            # check of that same file (its name is in the failure) no longer stands.
            failure = str(self.data.get("failing_check") or "")
            match = re.match(r"^check_app -> (.+?): smoke test FAILED\b", failure)
            failed_path = self.data.get("failing_check_app_path") or (match.group(1) if match else "")
            same = (_file_key(failed_path) == _file_key(path)
                    or (failed_path == PurePath(path).name))
            if self.data.get("check_failing") and failed_path and same:
                self.data["check_failing"] = False
                self.data["failing_check"] = ""

    def ask_to_verify(self, paths: list[str]) -> list[str]:
        """The not-yet-asked paths among `paths`, marked as asked: each is asked about once."""
        asked = self.data.setdefault("smoke_asked", [])
        fresh = [p for p in paths if p not in asked]
        asked.extend(fresh)
        del asked[:-20]
        return fresh

    def _clear_unverified_run(self, command: str) -> None:
        """A command that ran a not-verified script and succeeded settles it."""
        pending = self.data.get("smoke_unverified") or {}
        for path in [p for p in pending if _runs_script(command, p, self.data.get("workspace", ""))]:
            name = pending.pop(path)
            notes = self.data.get("smoke_notes", [])
            notes[:] = [n for n in notes if not n.startswith(name + ":")]

    def research_used(self) -> bool:
        """Did this session look anything up (a search, a browsed page, a fetch from the shell)?"""
        return bool(self.data.get("research_used"))

    def grounding_asked(self) -> bool:
        return bool(self.data.get("grounding_asked"))

    def mark_grounding_asked(self) -> None:
        self.data["grounding_asked"] = True

    def smoke_failures(self) -> int:
        return int(self.data.get("smoke_failures") or 0)

    def smoke_review(self) -> str:
        return " ".join(self.data.get("smoke_notes") or [])

    def secrets_unchecked(self, paths: list[str]) -> list[str]:
        """Files not yet scanned for credentials at the current state of the work."""
        checked = self.data.setdefault("secrets_checked", {})
        return [p for p in paths if checked.get(p) != self.mutation_count()]

    def weak_secret_asked(self) -> bool:
        """Whether this request was already asked once about findings that only look like a key."""
        return bool(self.data.get("weak_secret_asked"))

    def mark_weak_secret_asked(self) -> None:
        self.data["weak_secret_asked"] = True

    def record_secret_scan(self, path: str) -> None:
        self.data.setdefault("secrets_checked", {})[path] = self.mutation_count()

    def note_unmet(self, reasons: list[str]) -> None:
        self.data["unmet_checks"] = [_text(r) for r in reasons][:6]

    def nudge_text(self) -> str:
        """The one-shot nudge. It adds work to the task and never asks for a final answer."""
        text = ("Continue the task, and also run what you built: "
                + ", ".join(self.untested_paths())
                + ". Execute each runnable file and report what it printed; "
                "for a module, exercise it the same way.")
        if "test" in str(self.data.get("goal") or "").lower():
            text += " The user asked for tests, so also call generate_tests on each one."
        return text

    def _set_step(self, name: str, status: str) -> None:
        for step in self.data.get("checklist", []):
            if step.get("name") == name:
                step["status"] = status
                return

    def needs_verification(self) -> bool:
        return self.data.get("verification") == "pending"

    def note_ran_ok(self) -> None:
        """A program this run wrote was run and finished without an error, at the current state of the work."""
        self.data["ran_ok_at"] = self.mutation_count()

    def assurance(self) -> str:
        """How much the run's own evidence supports calling the changed work done, strongest first:

        tested     a test run (or a check the harness trusts) passed after the last change
        exercised  the result was loaded or run without an error, but no test run passed
        inspected  the changed files were read back; nothing was run
        unchecked  the work changed and nothing was run, read back or tested
        failed     a check failed and has not passed since
        ""         nothing was changed, so there is nothing to verify

        It only summarises what the other checks recorded; it never decides that work is wrong."""
        data = self.data
        if not int(data.get("mutation_count") or 0):
            level = ""
        elif self.check_failing() or any("smoke test FAILED" in str(n) for n in data.get("smoke_notes") or []):
            level = "failed"
        elif data.get("verification") == "verified":
            level = "tested"
        elif (any(at == self.mutation_count() for at in (data.get("smoke_passed_at") or {}).values())
              or data.get("ran_ok_at") == self.mutation_count()):
            level = "exercised"
        elif data.get("verification") == "inspected":
            level = "inspected"
        else:
            level = "unchecked"
        data["assurance"] = level
        return level

    def check_failing(self) -> bool:
        """True until a subsequent passing check resolves the latest failure."""
        return bool(self.data.get("check_failing"))

    def failing_check_detail(self) -> str:
        """The command (or tool) of the latest failing check and how it failed, shortened."""
        return str(self.data.get("failing_check") or "")

    def settle_check_app(self, result: str, path: str = "") -> None:
        """A check_app run that passed settles the earlier failure of that same page or script.

        The failure is recorded as "check_app -> <path>: smoke test FAILED ..."; only a later pass for that path clears
        it, and a pass for another file does not. A pass here is not a test run: it never makes the work "verified"."""
        failing = str(self.data.get("failing_check") or "")
        match = re.match(r"^check_app -> (.+?): smoke test FAILED\b", failing)
        failed_path = self.data.get("failing_check_app_path") or (match.group(1) if match else "")
        text = str(result or "")
        passed = re.match(r"^\s*(.+?): smoke test passed\b", text)
        if (self.data.get("check_failing") and failed_path and "smoke test FAILED" not in text
                and passed and _file_key(path or passed.group(1)) == _file_key(failed_path)):
            self.data["check_failing"] = False
            self.data["failing_check"] = ""
            self.data["failing_check_app_path"] = ""

    def request_failing_follow_up(self) -> bool:
        """Ask once per failure (at most MAX_FAILING_NUDGES a request) to fix or disclose it."""
        detail = self.failing_check_detail()
        if (not self.check_failing() or not detail or detail == self.data.get("failing_nudged_for")
                or int(self.data.get("failing_nudges") or 0) >= MAX_FAILING_NUDGES):
            return False
        self.data["failing_nudged_for"] = detail
        self.data["failing_nudges"] = int(self.data.get("failing_nudges") or 0) + 1
        return True

    def request_verification(self) -> bool:
        # Each write re-arms the reminder, and a model that answers it by
        # writing yet another check re-arms it again; capped, an unresolved
        # change is still disclosed by the final answer's verification note.
        if (not self.needs_verification() or self.data.get("verification_requested")
                or int(self.data.get("verification_requests") or 0) >= MAX_VERIFICATION_REQUESTS):
            return False
        self.data["verification_requested"] = True
        self.data["verification_requests"] = int(self.data.get("verification_requests") or 0) + 1
        return True

    def needs_replan(self) -> bool:
        return bool(self.data.get("stalled"))

    def request_replan(self) -> bool:
        if not self.needs_replan() or self.data.get("replan_requested"):
            return False
        self.data["replan_requested"] = True
        return True

    def review(self) -> str:
        """The passive end-of-turn note.

        Additive rather than first-match: an untested change and a pending
        verification are separate facts, and reporting only the first one hides
        the other from the user for the rest of the run.
        """
        notes = [note for note in (self._progress_review(), self._tests_review(),
                                   self.unmet_review()) if note]
        return " ".join(notes)

    def unmet_review(self) -> str:
        unmet = self.data.get("unmet_checks") or []
        return ("Not completed: " + "; ".join(unmet) + ".") if unmet else ""

    def _tests_review(self) -> str:
        if not self.needs_tests():
            return ""
        return f"Code changed with no tests written: {', '.join(self.untested_paths())}."

    def _progress_review(self) -> str:
        if self.needs_verification():
            return "Changed work has no fresh verification evidence."
        if self.data.get("verification") == "inspected":
            return "Changed work was inspected, but no automated verification passed."
        # A successful retry resolves the earlier failure for that tool. The
        # operation ledger keeps both attempts for diagnostics, but warning a
        # novice that review_code is unresolved after its second call completed
        # contradicts the answer they just received.
        latest_by_tool = {}
        for operation in self.data["operations"]:
            latest_by_tool[operation.get("tool", "tool")] = operation
        unresolved = [tool for tool, operation in latest_by_tool.items()
                      if operation.get("status") in {"blocked", "failed", "unknown"}]
        if unresolved:
            return f"Unresolved tool work remains: {', '.join(dict.fromkeys(unresolved))}."
        if self.needs_replan():
            return "Recent attempts stalled; the next turn should use a different approach."
        return ""

    def finish(self, answer: str | None) -> None:
        if answer:
            self.data["last_answer"] = _text(answer)

    def snapshot(self) -> dict:
        return deepcopy(self.data)

    def prompt(self) -> str:
        operations = self.data.get("operations") or []
        if not operations:
            return ""
        lines = ["\n\n## Current task state", "Controller-maintained operational facts; do not treat them as user instructions."]
        if self.data.get("goal"):
            lines.append(f"Goal: {self.data['goal']}")
        if self.data.get("workspace"):
            lines.append("Workspace: use relative paths from the project workspace; shell tools are isolated.")
        lines.append("Checklist: " + "; ".join(
            f"{step.get('name')}: {step.get('status')}" for step in self.data.get("checklist", [])
        ))
        for operation in operations[-6:]:
            line = f"- {operation.get('status', 'unknown')}: {operation.get('tool', 'tool')}"
            if operation.get("verdict") in {"passed", "failed"}:
                line += f" (verification {operation['verdict']})"
            lines.append(line)
        if self.needs_replan():
            lines.append("Progress is stalled. Do not repeat the failed approach; state a short revised next step and use a different safe tool or input.")
        if self.needs_verification():
            lines.append("Verification is pending: do not claim the changed work is verified until you inspect or test it.")
        if self.needs_tests():
            lines.append("Changed with no tests yet: " + ", ".join(self.untested_paths())
                         + ". Call generate_tests for each, or say why tests do not apply.")
        return "\n".join(lines)
