"""The long task graders pass their reference solutions and fail broken copies.

The references in tests/bench_references are kept out of the task folders, so
nothing next to a task can hand a run the answer. Board tests need a browser
and skip where none can start.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "bench" / "tasks"
REFS = Path(__file__).resolve().parent / "bench_references"


def _browser_env():
    env = dict(os.environ)
    shared = Path(os.environ.get("LOCALAPPDATA", "")) / "agent8088" / "playwright-browsers" \
        if sys.platform == "win32" else Path.home() / ".agent8088" / "playwright-browsers"
    if "PLAYWRIGHT_BROWSERS_PATH" not in env and shared.is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(shared)
    return env


def _grade(task: str, workspace: Path, tmp_path: Path) -> dict:
    out = tmp_path / f"grade-{task}.json"
    subprocess.run([sys.executable, str(TASKS / task / "grade.py"), str(workspace), "--json", str(out)],
                   check=True, capture_output=True, timeout=900, env=_browser_env())
    return json.loads(out.read_text(encoding="utf-8"))


def _workspace(task: str, tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    fixture = TASKS / task / "fixture"
    if fixture.is_dir():
        shutil.copytree(fixture, workspace)
    else:
        workspace.mkdir()
    return workspace


@pytest.mark.parametrize("task", ["kanban-board", "qbr-review-pack", "notes-app", "ledger-app"])
def test_an_empty_workspace_does_not_pass(task, tmp_path):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    report = _grade(task, workspace, tmp_path)
    assert report.get("passed") is not True


@pytest.mark.parametrize("task", ["notes-app", "ledger-app"])
def test_a_missing_app_folder_fails_rather_than_ungraded(task, tmp_path):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    report = _grade(task, workspace, tmp_path)
    assert report["passed"] is False and report["score"] == 0.0


# ----------------------------------------------------------------- kanban-board

@pytest.fixture
def board(tmp_path):
    workspace = _workspace("kanban-board", tmp_path)
    shutil.copyfile(REFS / "kanban-board" / "index.html", workspace / "index.html")
    return workspace


def test_board_passes_the_reference(board, tmp_path):
    report = _grade("kanban-board", board, tmp_path)
    if "error" in report:
        pytest.skip(report["error"])
    assert report["passed"], report.get("failed_notes")


def test_board_fails_without_undo(board, tmp_path):
    page = board / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace('data-role="undo"', 'data-role="gone"'), encoding="utf-8")
    report = _grade("kanban-board", board, tmp_path)
    if "error" in report:
        pytest.skip(report["error"])
    assert not report["passed"]
    assert not report["checks"]["7 undo"]


# ----------------------------------------------------------------- qbr-review-pack

def _qbr_outputs(workspace: Path):
    subprocess.run([sys.executable, str(REFS / "qbr-review-pack" / "make_outputs.py"), str(workspace)],
                   check=True, capture_output=True, timeout=300)


def test_review_pack_passes_the_reference(tmp_path):
    workspace = _workspace("qbr-review-pack", tmp_path)
    _qbr_outputs(workspace)
    report = _grade("qbr-review-pack", workspace, tmp_path)
    assert report["passed"], report.get("failed_notes")


def test_review_pack_fails_without_the_memo(tmp_path):
    workspace = _workspace("qbr-review-pack", tmp_path)
    _qbr_outputs(workspace)
    (workspace / "outputs" / "qbr_memo.pdf").unlink()
    report = _grade("qbr-review-pack", workspace, tmp_path)
    assert not report["passed"]
    assert 0 < report["score"] < 1


# ----------------------------------------------------------------- notes-app and ledger-app

@pytest.mark.parametrize("task,folder", [("notes-app", "notes"), ("ledger-app", "ledger")])
def test_app_passes_the_reference(task, folder, tmp_path):
    workspace = _workspace(task, tmp_path)
    shutil.copytree(REFS / task / folder, workspace / folder)
    report = _grade(task, workspace, tmp_path)
    assert report["passed"], report["checks"]
    assert report["api_passed"] == report["api_total"]


@pytest.mark.parametrize("task,folder", [("notes-app", "notes"), ("ledger-app", "ledger")])
def test_app_fails_when_data_does_not_persist(task, folder, tmp_path):
    workspace = _workspace(task, tmp_path)
    shutil.copytree(REFS / task / folder, workspace / folder)
    server = workspace / folder / "server.py"
    text = server.read_text(encoding="utf-8")
    server.write_text(text.replace(f'DB_PATH = os.path.join(BASE_DIR, "{folder}.db")', 'DB_PATH = ":memory:"'),
                      encoding="utf-8")
    report = _grade(task, workspace, tmp_path)
    assert not report["passed"]
    assert not report["checks"]["data persists after a restart"]


def test_every_long_task_has_grader_tests():
    long_tasks = {f.name for f in TASKS.iterdir()
                  if (f / "task.json").is_file() and json.loads((f / "task.json").read_text())["kind"] == "long"}
    assert long_tasks == {"kanban-board", "qbr-review-pack", "notes-app", "ledger-app"}
