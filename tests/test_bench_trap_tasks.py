"""The trap task graders pass right work in more than one shape, and fail work that missed a trap.

A trap task has a defect a run that does not check its own work will miss, stated in the prompt so a careful run can
find it. Each grader is tested with:
- a reference solution
- a different but valid one, so the grader does not demand one shape of answer
- solutions that missed each trap, which must fail although they run cleanly and look finished
- (invoice-totals) totals typed in for the task's own files, which must fail on exports the script has not seen

The references live in tests/bench_references/<task>/<solution>/.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "bench" / "tasks"
REFERENCES = Path(__file__).resolve().parent / "bench_references"
TRAP_TASKS = {"invoice-totals", "discount-rounding"}


def _workspace(task: str, solution: str, tmp_path: Path) -> Path:
    workspace = tmp_path / f"{task}-{solution}"
    shutil.copytree(TASKS / task / "fixture", workspace)
    for path in (REFERENCES / task / solution).iterdir():
        if path.is_file():
            shutil.copy(path, workspace / path.name)
    return workspace


def _grade(task: str, workspace: Path, tmp_path: Path) -> dict:
    out = tmp_path / f"grade-{task}.json"
    subprocess.run([sys.executable, str(TASKS / task / "grade.py"), str(workspace), "--json", str(out)],
                   check=True, capture_output=True, timeout=300)
    return json.loads(out.read_text(encoding="utf-8"))


def _run(workspace: Path, *args):
    subprocess.run([sys.executable, *args], cwd=workspace, check=True, capture_output=True, timeout=120)


# --------------------------------------------------------------------------------------------------- invoice-totals

@pytest.mark.parametrize("solution", ["reference", "variant"])
def test_invoice_totals_passes_right_work(solution, tmp_path):
    workspace = _workspace("invoice-totals", solution, tmp_path)
    _run(workspace, "monthly_totals.py")
    report = _grade("invoice-totals", workspace, tmp_path)
    assert report["passed"], report["checks"]


@pytest.mark.parametrize("solution, failed", [
    ("trap_missed_duplicates", "each invoice is counted once"),
    ("trap_missed_commas", "the monthly totals are right"),
])
def test_invoice_totals_fails_a_missed_trap(solution, failed, tmp_path):
    workspace = _workspace("invoice-totals", solution, tmp_path)
    _run(workspace, "monthly_totals.py")   # it runs cleanly: the miss is in the numbers
    report = _grade("invoice-totals", workspace, tmp_path)
    assert not report["passed"]
    assert report["checks"]["the script runs"]
    assert not report["checks"][failed]


def test_invoice_totals_fails_totals_typed_in_for_these_files(tmp_path):
    sys.path.insert(0, str(TASKS / "invoice-totals"))
    try:
        import grade as invoice_grade
    finally:
        sys.path.pop(0)
    rows = invoice_grade.expected(TASKS / "invoice-totals" / "fixture" / "invoices")
    workspace = _workspace("invoice-totals", "reference", tmp_path)
    lines = ["month,invoices,total"] + [f"{m},{c},{t}" for m, c, t in rows]
    (workspace / "monthly_totals.py").write_text(
        f"open('monthly_totals.csv', 'w').write({chr(10).join(lines) + chr(10)!r})\n", encoding="utf-8")
    _run(workspace, "monthly_totals.py")
    report = _grade("invoice-totals", workspace, tmp_path)
    assert report["checks"]["the monthly totals are right"]
    assert not report["checks"]["right on exports it has not seen"]
    assert not report["passed"]


def test_the_fixture_holds_both_traps():
    folder = TASKS / "invoice-totals" / "fixture" / "invoices"
    text = "".join(p.read_text(encoding="utf-8") for p in folder.glob("*.csv"))
    ids = [line.split(",")[0] for line in text.splitlines() if line and not line.startswith("invoice_id")]
    normal = ["".join(i.split()).upper() for i in ids]
    assert len(normal) > len(set(normal)), "no invoice appears twice"
    assert any(i != "".join(i.split()).upper() for i in ids), "no id written another way"
    assert '",' in text or '"\n' in text, "no comma decimal"


# ------------------------------------------------------------------------------------------------ discount-rounding

@pytest.mark.parametrize("solution", ["reference", "variant"])
def test_discount_rounding_passes_right_work(solution, tmp_path):
    workspace = _workspace("discount-rounding", solution, tmp_path)
    report = _grade("discount-rounding", workspace, tmp_path)
    assert report["passed"], report["checks"]


@pytest.mark.parametrize("solution, failed", [
    ("trap_missed_float", "exact on prices floating point gets wrong"),
    ("trap_missed_end", "rounded after each discount"),
])
def test_discount_rounding_fails_a_missed_trap(solution, failed, tmp_path):
    workspace = _workspace("discount-rounding", solution, tmp_path)
    report = _grade("discount-rounding", workspace, tmp_path)
    assert not report["passed"]
    assert report["checks"]["the task's own tests pass"]   # the given tests do not show the miss
    assert not report["checks"][failed]


def test_discount_rounding_needs_tests_of_the_runs_own(tmp_path):
    workspace = _workspace("discount-rounding", "reference", tmp_path)
    shutil.copy(TASKS / "discount-rounding" / "fixture" / "test_pricing.py", workspace / "test_pricing.py")
    report = _grade("discount-rounding", workspace, tmp_path)
    assert not report["checks"]["the run added tests of its own"]


@pytest.mark.parametrize("solution", ["reference", "variant", "trap_missed_float", "trap_missed_end"])
def test_every_discount_solution_passes_the_tests_that_came_with_the_task(solution, tmp_path):
    workspace = _workspace("discount-rounding", solution, tmp_path)
    shutil.copy(TASKS / "discount-rounding" / "fixture" / "test_pricing.py", workspace / "test_given.py")
    probe = ("import test_given as t; [getattr(t, n)() for n in dir(t) if n.startswith('test_')]")
    _run(workspace, "-c", probe)
