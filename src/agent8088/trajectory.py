"""Small, session-scoped operational state for the shared agent loop."""

from __future__ import annotations

from copy import deepcopy


VERSION = 2
MAX_OPERATIONS = 12
MAX_TEXT = 240
MAX_EVIDENCE = 6


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
                   blocked: bool, mutated: bool, verdict: str = "unknown") -> None:
        operation["status"] = "blocked" if blocked else "failed" if failed else "done"
        operation["result"] = _text(result)
        operation["verdict"] = verdict if verdict in {"passed", "failed"} else "unknown"
        if operation["status"] == "done":
            self._set_step("Inspect the current state", "done")
            self.data["consecutive_setbacks"] = 0
            self.data["stalled"] = False
        else:
            self.data["consecutive_setbacks"] = int(self.data.get("consecutive_setbacks") or 0) + 1
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

    def _set_step(self, name: str, status: str) -> None:
        for step in self.data["checklist"]:
            if step.get("name") == name:
                step["status"] = status
                return

    def needs_verification(self) -> bool:
        return self.data.get("verification") == "pending"

    def request_verification(self) -> bool:
        if not self.needs_verification() or self.data.get("verification_requested"):
            return False
        self.data["verification_requested"] = True
        return True

    def needs_replan(self) -> bool:
        return bool(self.data.get("stalled"))

    def request_replan(self) -> bool:
        if not self.needs_replan() or self.data.get("replan_requested"):
            return False
        self.data["replan_requested"] = True
        return True

    def review(self) -> str:
        if self.needs_verification():
            return "Changed work has no fresh verification evidence."
        if self.data.get("verification") == "inspected":
            return "Changed work was inspected, but no automated verification passed."
        unresolved = [op.get("tool", "tool") for op in self.data["operations"]
                      if op.get("status") in {"blocked", "failed", "unknown"}]
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
            f"{step.get('name')}: {step.get('status')}" for step in self.data["checklist"]
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
        return "\n".join(lines)
