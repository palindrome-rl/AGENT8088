"""Grader for invoice-totals, a trap task.

    python grade.py <workspace> --json <report.json>

The exports hold the two traps the prompt states: an invoice exported by two systems with its id written another
way, and amounts with a comma as the decimal mark. A run that checks its own totals finds both; one that does not
reports totals that look right and are not.

Checks, from the original exports kept next to this file:
- monthly_totals.py exists, runs on the task's exports and writes the right monthly_totals.csv
- the same script, run on a second set of exports it has never seen (hidden/), is right too, so totals typed in by
  hand or a script fitted to these files do not pass
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent


def expected(folder: Path) -> list[tuple[str, int, str]]:
    """(month, invoices, total) per month, oldest first: each invoice once, comma decimals read as decimals."""
    seen = {}
    for path in sorted(folder.glob("*.csv")):
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = re.sub(r"\s+", "", row["invoice_id"]).upper()
                seen.setdefault(key, (row["date"][:7], Decimal(row["amount"].strip().replace(",", "."))))
    months = {}
    for month, amount in seen.values():
        count, total = months.get(month, (0, Decimal(0)))
        months[month] = (count + 1, total + amount)
    return [(m, c, f"{t:.2f}") for m, (c, t) in sorted(months.items())]


def read_output(path: Path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        return [(r["month"].strip(), int(r["invoices"]), f"{Decimal(r['total'].strip()):.2f}") for r in rows]
    except (OSError, KeyError, ValueError, TypeError, ArithmeticError):
        return None


def run_on(script: Path, invoices: Path) -> tuple[bool, list | None]:
    """Run the script in a fresh folder that holds only `invoices/`, and read what it wrote."""
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        shutil.copy(script, work / "monthly_totals.py")
        shutil.copytree(invoices, work / "invoices")
        done = subprocess.run([sys.executable, "monthly_totals.py"], cwd=work, capture_output=True, text=True,
                              timeout=120, check=False)
        return done.returncode == 0, read_output(work / "monthly_totals.csv")


def grade(workspace: Path) -> dict:
    checks = {}
    script = workspace / "monthly_totals.py"
    checks["monthly_totals.py exists"] = script.is_file()
    want = expected(HERE / "fixture" / "invoices")
    left = read_output(workspace / "monthly_totals.csv")
    checks["monthly_totals.csv in the folder is right"] = left == want
    ran = got = None
    if script.is_file():
        ran, got = run_on(script, HERE / "fixture" / "invoices")
    checks["the script runs"] = bool(ran)
    checks["each invoice is counted once"] = bool(got) and [r[:2] for r in got] == [r[:2] for r in want]
    checks["the monthly totals are right"] = got == want
    hidden = None
    if script.is_file():
        _ok, hidden = run_on(script, HERE / "hidden" / "invoices")
    checks["right on exports it has not seen"] = hidden == expected(HERE / "hidden" / "invoices")
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
