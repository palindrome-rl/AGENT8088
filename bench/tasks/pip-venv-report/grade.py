"""Grader for pip-venv-report.

    python grade.py <workspace> --json <report.json>

Checks the work on its own, from the original sales.csv kept next to this file:
the venv exists and has tabulate, report.py runs with the venv's Python,
totals.json is right, and the printed table has every region in revenue order.
"""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # bench/, for graderlib
import graderlib  # noqa: E402


def expected():
    totals = {}
    with open(HERE / "fixture" / "sales.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            entry = totals.setdefault(row["region"], {"units": 0, "revenue": 0.0})
            entry["units"] += int(row["units"])
            entry["revenue"] += int(row["units"]) * float(row["unit_price"])
    for entry in totals.values():
        entry["revenue"] = round(entry["revenue"], 2)
    return totals


def venv_python(workspace: Path):
    """The command that starts the venv's Python (see graderlib.venv_python_command), or None."""
    return graderlib.venv_python_command(workspace)


def grade(workspace: Path) -> dict:
    checks = {}
    want = expected()
    python = venv_python(workspace)
    checks["venv exists"] = python is not None
    has_tabulate = False
    if python:
        probe = subprocess.run([*python, "-c", "import tabulate"], cwd=workspace, capture_output=True, timeout=60,
                               check=False)
        has_tabulate = probe.returncode == 0
    checks["tabulate installed in the venv"] = has_tabulate
    checks["report.py exists"] = (workspace / "report.py").is_file()

    # Run it fresh: remove the agent's totals.json so this run has to write it.
    output = ""
    totals_path = workspace / "totals.json"
    ran = False
    if python and checks["report.py exists"]:
        previous = totals_path.read_bytes() if totals_path.is_file() else None
        totals_path.unlink(missing_ok=True)
        done = subprocess.run([*python, "report.py"], cwd=workspace, capture_output=True, text=True,
                              timeout=120, encoding="utf-8", errors="replace", check=False)
        output = done.stdout
        ran = done.returncode == 0
        if not totals_path.is_file() and previous is not None:
            totals_path.write_bytes(previous)
    checks["report.py runs with the venv"] = ran

    try:
        got = json.loads(totals_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    right = isinstance(got, dict) and set(got) == set(want) and all(
        isinstance(got[r], dict) and int(got[r].get("units", -1)) == want[r]["units"]
        and abs(float(got[r].get("revenue", -1)) - want[r]["revenue"]) < 0.011 for r in want)
    checks["totals.json is right"] = bool(right)

    order = sorted(want, key=lambda r: want[r]["revenue"], reverse=True)
    positions = [output.find(region) for region in order]
    checks["table lists every region by revenue"] = all(p >= 0 for p in positions) and positions == sorted(positions)
    checks["table shows the revenue figures"] = all(f"{want[r]['revenue']:.2f}" in output
                                                     or str(want[r]["revenue"]) in output for r in want)

    score = sum(checks.values()) / len(checks)
    return {"passed": all(checks.values()), "score": round(score, 3), "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    report = grade(Path(args.workspace))
    Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(main())
