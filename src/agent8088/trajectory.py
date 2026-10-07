"""Small, session-scoped operational state for the shared agent loop."""

from __future__ import annotations

from copy import deepcopy
from pathlib import PurePath


VERSION = 2
MAX_OPERATIONS = 12
MAX_TEXT = 240
MAX_EVIDENCE = 6


MAX_UNTESTED = 8
MAX_VERIFICATION_REQUESTS = 2

# Suffixes worth a test. Config, data, docs and markup are excluded because a
# generated test for them asserts that a file parses, which nobody needed.
CODE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".java",
    ".kt", ".cs", ".php", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp",
})


def _is_test_path(path: str) -> bool:
    p = PurePath(path)
    stem = p.stem.lower()
    return (stem.startswith("test_") or stem.endswith("_test")
            or ".test" in p.name.lower() or ".spec" in p.name.lower()
            or any(part.lower() in {"tests", "test", "__tests__", "spec"}
                   for part in p.parts))


def _is_code_path(path: str) -> bool:
    return PurePath(path).suffix.lower() in CODE_SUFFIXES


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
        "tests_requested": False,
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
                   path: str = "") -> None:
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
        if mutated:
            self.data["mutation_count"] = int(self.data.get("mutation_count") or 0) + 1
            self.data["verification"] = "pending"
            self.data["verification_requested"] = False
            self._set_step("Complete the requested work", "done")
            self._set_step("Verify changed work", "pending")
        if operation["verdict"] == "passed":
            self.data["evidence"].append({"tool": operation["tool"], "result": operation["result"]})
            del self.data["evidence"][:-MAX_EVIDENCE]
            if self.needs_verification():
                self.data["verification"] = "verified"
                self._set_step("Verify changed work", "done")
        elif (self.needs_verification() and operation["status"] == "done"
              and not mutated and operation["tool"] in {"read_text", "execute_shell", "git_diff"}):
            self.data["verification"] = "inspected"
            self._set_step("Verify changed work", "inspected")
            self.data["evidence"].append({"tool": operation["tool"], "result": operation["result"]})
            del self.data["evidence"][:-MAX_EVIDENCE]
        if self.data["consecutive_setbacks"] >= 2:
            self.data["stalled"] = True
        self._track_tests(operation, path, mutated=mutated)

    def _track_tests(self, operation: dict, path: str, *, mutated: bool) -> None:
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

        if path not in untested:
            untested.append(path)
            del untested[:-MAX_UNTESTED]
            # A new untested change re-arms the one-shot: the previous nudge was
            # about different files.
            self.data["tests_requested"] = False

    def changed_code_paths(self) -> list[str]:
        """Code files this run wrote, newest last."""
        return list(self.data.get("changed_code") or [])

    def untested_paths(self) -> list[str]:
        return list(self.data.get("untested_changes") or [])

    def needs_tests(self) -> bool:
        return bool(self.data.get("untested_changes"))

    def request_tests(self) -> bool:
        if not self.needs_tests() or self.data.get("tests_requested"):
            return False
        self.data["tests_requested"] = True
        return True

    def _set_step(self, name: str, status: str) -> None:
        for step in self.data.get("checklist", []):
            if step.get("name") == name:
                step["status"] = status
                return

    def needs_verification(self) -> bool:
        return self.data.get("verification") == "pending"

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
        notes = [note for note in (self._progress_review(), self._tests_review()) if note]
        return " ".join(notes)

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
