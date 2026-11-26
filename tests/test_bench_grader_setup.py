"""What the graders need from the machine that runs them.

Seen in a Tier 1 run on Windows with the WSL sandbox: work that passed was graded as failed, for two reasons that
had nothing to do with the work. The runner's venv had no pytest, so every "the given tests pass" check failed; and
the venv the agent built inside WSL has bin/python, a link Windows cannot start, so "venv exists" failed.
"""
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH))

import benchlib  # noqa: E402
import graderlib  # noqa: E402
import run  # noqa: E402


def _linux_venv(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".venv" / "bin").mkdir(parents=True)
    (workspace / ".venv" / "bin" / "python").write_text("link to /usr/bin/python3", encoding="utf-8")
    return workspace


def test_a_windows_venv_runs_as_is(tmp_path):
    python = tmp_path / "ws" / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    assert graderlib.venv_python_command(tmp_path / "ws", windows=True) == [str(python)]


def test_a_venv_built_in_wsl_runs_in_the_distro(tmp_path):
    workspace = _linux_venv(tmp_path)
    command = graderlib.venv_python_command(workspace, distro="agent8088-sandbox-test", wsl="wsl.exe", windows=True)
    where = graderlib.to_wsl_path(workspace)
    assert command == ["wsl.exe", "-d", "agent8088-sandbox-test", "--cd", where, "--exec", f"{where}/.venv/bin/python"]


def test_without_a_distro_a_linux_venv_cannot_run_on_windows(tmp_path):
    assert graderlib.venv_python_command(_linux_venv(tmp_path), distro="", wsl="wsl.exe", windows=True) is None


def test_on_linux_bin_python_runs_as_is(tmp_path):
    workspace = _linux_venv(tmp_path)
    assert graderlib.venv_python_command(workspace, windows=False) == [str(workspace / ".venv" / "bin" / "python")]


def test_no_venv_is_none(tmp_path):
    assert graderlib.venv_python_command(tmp_path, windows=True) is None


@pytest.mark.parametrize("path, wsl", [(r"E:\Palindrome Labs\runs\ws", "/mnt/e/Palindrome Labs/runs/ws"),
                                       (r"c:\x", "/mnt/c/x"), ("/home/a", "/home/a")])
def test_windows_paths_as_wsl_sees_them(path, wsl):
    assert graderlib.to_wsl_path(path) == wsl


def test_the_grader_is_told_the_profiles_distro(monkeypatch):
    monkeypatch.delenv(graderlib.GRADER_WSL_DISTRO_ENV, raising=False)
    env = run.grader_env({"config": {"sandbox_backend": "wsl", "wsl_distro": "agent8088-sandbox-test"}})
    assert env[graderlib.GRADER_WSL_DISTRO_ENV] == "agent8088-sandbox-test"
    assert run.grader_env({})[graderlib.GRADER_WSL_DISTRO_ENV] == "agent8088-sandbox"


def test_tasks_name_the_modules_their_graders_import():
    tasks = {task["id"]: task for task in benchlib.discover_tasks(BENCH / "tasks")}
    for task_id in ("broken-test-green", "no-change-needed", "impossible-request"):
        assert "pytest" in tasks[task_id]["grader_modules"]
    for task_id in ("tile-flip-game", "cart-page", "sales-bar-chart"):
        assert "playwright" in tasks[task_id]["grader_modules"]


def test_a_missing_grader_module_is_found():
    tasks = [{"grader_modules": ["json", "no_such_module_for_a_grader"]}, {"grader_modules": []}]
    assert benchlib.missing_grader_modules(tasks) == ["no_such_module_for_a_grader"]


def test_the_runner_stops_before_any_run_when_a_grader_module_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(benchlib, "missing_grader_modules", lambda tasks: ["pytest"])
    monkeypatch.setattr(run, "run_one", lambda *a, **k: pytest.fail("a run started"))
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps({"name": "p", "config": {}}), encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        run.main(["--profile", str(profile), "--task", "broken-test-green", "--out", str(tmp_path / "out")])
    assert stop.value.code == 2
    assert "pytest" in capsys.readouterr().err
