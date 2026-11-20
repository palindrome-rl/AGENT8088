"""Grader for notes-app: runs the task's app checker (check.py) and reports its result.

    python grade.py <workspace> --json <report.json>

check.py is the original checker, unchanged except for how it starts a browser.
It starts the server on a free port, runs its API flow, checks the page is
served, restarts the server to check persistence, and runs the app's own tests.
The run passes when check.py's all_pass is true; the score is the share of
those checks that passed.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib


def grade(workspace: Path) -> dict:
    report = graderlib.run_checker(HERE / "check.py", [workspace])
    if "server_started" not in report:
        if "notes" in str(report.get("error", "")):
            return {"passed": False, "score": 0.0, "checks": {"notes/server.py exists": False}}
        return {"error": report.get("error") or "checker gave no report"}
    checks = {"server starts": bool(report.get("server_started"))}
    for name, step in (report.get("api") or {}).items():
        checks[f"api: {name}"] = bool(step.get("ok"))
    checks["page is served"] = bool((report.get("page") or {}).get("served"))
    checks["data persists after a restart"] = bool(report.get("persisted_after_restart"))
    checks["the app's own tests pass"] = (report.get("own_tests") or {}).get("returncode") == 0
    return {"passed": bool(report.get("all_pass")), "score": round(sum(checks.values()) / len(checks), 3),
            "checks": checks, "api_passed": report.get("api_passed"), "api_total": report.get("api_total")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    graderlib.write(grade(Path(args.workspace)), args.json)


if __name__ == "__main__":
    sys.exit(main())
