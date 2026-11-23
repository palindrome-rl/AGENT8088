"""Grader for price-watch: runs the task's scenario checker (check.py) and reports its result.

    python grade.py <workspace> --state <state folder> --json <report.json>

The runner starts the task's service first and passes the folder that holds its
state. check.py is the original checker, unchanged except for its imports.
It reads the workbook, the brief and the schedule, and runs the agent's watch.py
against the mock sites twice, switching their prices from day 1 to day 2 in the
state folder, which it then sets back to day 1.
The run passes only if every scenario passes; the score is the share that did.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib


def grade(workspace: Path, state: Path) -> dict:
    report = graderlib.run_checker(HERE / "check.py", [workspace, state])
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        # No deliverable at all is a failure; a checker that broke is ungraded.
        if "score" in report or "score_60" in report:
            return {"passed": False, "score": 0.0, "checks": {}, "detail": report.get("error")}
        return {"error": report.get("error") or "checker gave no scenarios"}
    checks = {item["name"]: bool(item["ok"]) for item in scenarios}
    notes = {item["name"]: item.get("note") for item in scenarios if not item["ok"]}
    return {
        "passed": bool(checks) and all(checks.values()),
        "score": round(sum(checks.values()) / len(checks), 3),
        "checks": checks,
        "failed_notes": notes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--state", required=True, help="the folder that holds the service's state")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    graderlib.write(grade(Path(args.workspace), Path(args.state)), args.json)


if __name__ == "__main__":
    sys.exit(main())
