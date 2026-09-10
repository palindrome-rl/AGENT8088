"""Small, session-scoped operational state for the shared agent loop."""

from __future__ import annotations

from copy import deepcopy


VERSION = 1
MAX_OPERATIONS = 12
MAX_TEXT = 240


def _text(value) -> str:
    return " ".join(str(value or "").split())[:MAX_TEXT]


def _blank(goal: str = "") -> dict:
    return {
        "version": VERSION,
        "goal": _text(goal),
        "operations": [],
        "mutation_count": 0,
        "verification": "not_needed",
        "verification_requested": False,
        "message_count": 0,
    }


class TrajectoryState:
    """Mutates one JSON-serializable session dictionary in place.

    The controller deliberately records only operational facts. Full arguments
    and tool output already belong in the transcript; duplicating them here
    would make a restart-state file both noisier and riskier.
    """

    def __init__(self, state: dict | None, goal: str = "", message_count: int = 0):
        self.data = state if isinstance(state, dict) else {}
        self._normalise(goal, message_count)

    def _normalise(self, goal: str, message_count: int) -> None:
        if self.data.get("version") != VERSION or not isinstance(self.data.get("operations"), list):
            self.data.clear()
            self.data.update(_blank(goal))
        previous_count = int(self.data.get("message_count") or 0)
        unresolved = (self.data.get("verification") == "pending" or any(
            op.get("status") in {"running", "blocked", "failed", "unknown"}
            for op in self.data["operations"]
        ))
        # A finished turn followed by a new human message starts a fresh goal.
        # Blocked/unknown work remains active so a permission reply can continue it.
        if goal and (not self.data.get("goal") or (message_count > previous_count and not unresolved)):
            self.data.clear()
            self.data.update(_blank(goal))
        self.data["message_count"] = message_count
        for operation in self.data["operations"]:
            if operation.get("status") == "running":
                operation["status"] = "unknown"

    def before_tool(self, name: str) -> dict:
        operation = {
            "tool": _text(name),
            "status": "running",
        }
        self.data["operations"].append(operation)
        del self.data["operations"][:-MAX_OPERATIONS]
        return operation

    def after_tool(self, operation: dict, result: str, *, failed: bool,
                   blocked: bool, mutated: bool, verdict: str = "unknown") -> None:
        if blocked:
            operation["status"] = "blocked"
        elif failed:
            operation["status"] = "failed"
        else:
            operation["status"] = "done"
        operation["result"] = _text(result)
        operation["verdict"] = verdict if verdict in {"passed", "failed"} else "unknown"
        if mutated:
            self.data["mutation_count"] = int(self.data.get("mutation_count") or 0) + 1
            self.data["verification"] = "pending"
            self.data["verification_requested"] = False
        if operation["verdict"] == "passed" and self.needs_verification():
            self.data["verification"] = "verified"

    def needs_verification(self) -> bool:
        return self.data.get("verification") == "pending"

    def request_verification(self) -> bool:
        if not self.needs_verification() or self.data.get("verification_requested"):
            return False
        self.data["verification_requested"] = True
        return True

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
        for operation in operations[-6:]:
            line = f"- {operation.get('status', 'unknown')}: {operation.get('tool', 'tool')}"
            if operation.get("verdict") in {"passed", "failed"}:
                line += f" (verification {operation['verdict']})"
            lines.append(line)
        if self.needs_verification():
            lines.append("Verification is pending: do not claim the changed work is verified until you inspect or test it.")
        return "\n".join(lines)
