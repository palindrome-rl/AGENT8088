"""Each benchmark grader passes a reference solution and fails broken ones.

A grader that passes bad work would hide false-done runs, and one that fails
good work would invent false not-verified runs, so each grader is checked both
ways. The venv is made offline (--without-pip) with a small stand-in for the
PyPI package, so these tests need no network.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TASKS = Path(__file__).resolve().parent.parent / "bench" / "tasks"

REPORT = '''import csv, json
from tabulate import tabulate
totals = {}
with open("sales.csv", newline="") as handle:
    for row in csv.DictReader(handle):
        entry = totals.setdefault(row["region"], {"units": 0, "revenue": 0.0})
        entry["units"] += int(row["units"])
        entry["revenue"] += int(row["units"]) * float(row["unit_price"])
for entry in totals.values():
    entry["revenue"] = round(entry["revenue"], 2)
rows = sorted(((r, v["units"], f"{v['revenue']:.2f}") for r, v in totals.items()), key=lambda x: -float(x[2]))
print(tabulate(rows, headers=["region", "units", "revenue"]))
json.dump(totals, open("totals.json", "w"))
'''

STUB_TABULATE = '''def tabulate(rows, headers=()):
    return "\\n".join(" | ".join(str(c) for c in row) for row in [tuple(headers)] + [tuple(r) for r in rows])
'''


def _grade(task: str, workspace: Path, tmp_path: Path) -> dict:
    out = tmp_path / "grade.json"
    subprocess.run([sys.executable, str(TASKS / task / "grade.py"), str(workspace), "--json", str(out)],
                   check=True, capture_output=True, timeout=300)
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture
def venv_workspace(tmp_path):
    workspace = tmp_path / "ws"
    shutil.copytree(TASKS / "pip-venv-report" / "fixture", workspace)
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(workspace / ".venv")], check=True, timeout=180)
    python = next(p for p in (workspace / ".venv" / "Scripts" / "python.exe", workspace / ".venv" / "bin" / "python")
                  if p.exists())
    purelib = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             check=True, capture_output=True, text=True).stdout.strip()
    Path(purelib).mkdir(parents=True, exist_ok=True)
    (Path(purelib) / "tabulate.py").write_text(STUB_TABULATE, encoding="utf-8")
    return workspace


def test_pip_venv_report_passes_a_reference_solution(venv_workspace, tmp_path):
    (venv_workspace / "report.py").write_text(REPORT, encoding="utf-8")
    report = _grade("pip-venv-report", venv_workspace, tmp_path)
    assert report["passed"], report["checks"]
    assert report["score"] == 1.0


def test_pip_venv_report_fails_wrong_totals(venv_workspace, tmp_path):
    (venv_workspace / "report.py").write_text(REPORT.replace('int(row["units"]) * float', 'float'), encoding="utf-8")
    report = _grade("pip-venv-report", venv_workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["totals.json is right"]


def test_pip_venv_report_fails_without_the_package(venv_workspace, tmp_path):
    for stub in venv_workspace.joinpath(".venv").rglob("tabulate.py"):
        stub.unlink()
    (venv_workspace / "report.py").write_text(REPORT, encoding="utf-8")
    report = _grade("pip-venv-report", venv_workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["tabulate installed in the venv"]


def test_pip_venv_report_does_not_credit_a_stale_totals_file(venv_workspace, tmp_path):
    # A right totals.json left behind by a script that no longer runs is not a pass.
    good = tmp_path / "good"
    shutil.copytree(venv_workspace, good)
    (good / "report.py").write_text(REPORT, encoding="utf-8")
    _grade("pip-venv-report", good, tmp_path)
    shutil.copyfile(good / "totals.json", venv_workspace / "totals.json")
    (venv_workspace / "report.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    report = _grade("pip-venv-report", venv_workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["report.py runs with the venv"]


def test_a_grader_gives_a_report_for_an_empty_workspace(tmp_path):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    report = _grade("pip-venv-report", workspace, tmp_path)
    assert report["passed"] is False
    assert report["score"] == 0.0
