"""Small, session-scoped operational state for the shared agent loop."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePath

from agent8088.completion import SUBGOAL_PREFIX

VERSION = 2
MAX_OPERATIONS = 12
MAX_TEXT = 240
MAX_EVIDENCE = 6


MAX_WRITTEN = 50
MAX_UNTESTED = 8
MAX_VERIFICATION_REQUESTS = 2

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
        "subgoals_built": False,
        "unmet_checks": [],
        "smoke_notes": [],
        "smoke_failures": 0,
        "smoke_checked": {},
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
            # The old request's parts and smoke notes describe work the person
            # has moved on from: carrying them over nudges the new request about
            # the old one and stamps stale notes on its answer.
            self.data["subgoals_built"] = False
            self.data["checklist"] = [step for step in self.data.get("checklist", [])
                                      if not str(step.get("name", "")).startswith(SUBGOAL_PREFIX)]
            self.data["smoke_notes"] = []
            self.data["smoke_checked"] = {}
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
                   path: str = "", command: str = "", existed: bool = False) -> None:
        operation["status"] = "blocked" if blocked else "failed" if failed else "done"
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
        if mutated and path and operation["status"] == "done":
            # Where the work actually landed. The answer quotes this, because a
            # run whose writes were redirected into the sandbox workspace reads
            # as a false completion claim when the user checks the folder they
            # named and finds it empty.
            written = self.data.setdefault("written_paths", [])
            if path not in written:
                written.append(path)
                del written[:-MAX_WRITTEN]
        # Changing a file is not evidence that the failed check is fixed.
        # Keep the failure visible until a subsequent check actually passes,
        # including failures from checks that also modify the workspace.
        if operation["verdict"] == "failed":
            self.data["check_failing"] = True
        elif operation["verdict"] == "passed":
            self.data["check_failing"] = False
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
        if _is_test_path(path) and path not in created:
            edited = self.data.setdefault("edited_existing_tests", [])
            if path not in edited:
                edited.append(path)
                del edited[:-MAX_WRITTEN]

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
        return bool(self.needs_verification() or self.unresolved_tools() or self.open_subgoals())

    def progress_mark(self) -> tuple:
        """A value that changes when the run makes real progress."""
        return (int(self.data.get("mutation_count") or 0),
                int(self.data.get("passed_checks") or 0),
                sum(1 for step in self.subgoals() if step.get("status") == "done"))

    def smoke_unchecked(self, paths: list[str]) -> list[str]:
        """Deliverables not yet smoke-tested at the current state of the work."""
        checked = self.data.setdefault("smoke_checked", {})
        return [p for p in paths if checked.get(p) != self.mutation_count()]

    def record_smoke(self, path: str, summary: str, failed: bool) -> None:
        self.data.setdefault("smoke_checked", {})[path] = self.mutation_count()
        notes = self.data.setdefault("smoke_notes", [])
        notes[:] = [n for n in notes if not n.startswith(summary.split(":", 1)[0] + ":")]
        notes.append(_text(summary))
        del notes[:-4]
        if failed:
            self.data["smoke_failures"] = int(self.data.get("smoke_failures") or 0) + 1

    def smoke_failures(self) -> int:
        return int(self.data.get("smoke_failures") or 0)

    def smoke_review(self) -> str:
        return " ".join(self.data.get("smoke_notes") or [])

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

    def check_failing(self) -> bool:
        """True until a subsequent passing check resolves the latest failure."""
        return bool(self.data.get("check_failing"))

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
