"""Auto QA: the harness runs the project's own tests after the last change instead of only asking the model to.

The end-of-run checks already load pages, start scripts and probe servers by themselves; for a test suite they only
asked ("run `python -m pytest -q` now"), which costs a turn and is often skipped. Now, when changed work has no fresh
check and a suite sits above the changed files, the harness runs it in the sandbox, bounded by auto_qa_seconds, once
per state of the work:

- passed: the change is verified (tested) and no turn is spent asking;
- failed: the model is sent back with the command and the tail of the failure;
- no suite, or a result that says nothing: the old ask stays.

Not in the last stretch of the clock, and not with auto_qa=0.
"""
from pathlib import Path

import pytest

from agent8088 import completion as c
from agent8088.trajectory import TrajectoryState


@pytest.fixture
def project(engine, monkeypatch, tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    assert 1 + 1 == 2\n")
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "AUTO_QA", True)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_active_budget", None)
    ran = []

    def fake(command, timeout=25, python_code=False, image=""):
        ran.append((command, timeout))
        return engine._AUTO_QA_FAKE_RESULT
    monkeypatch.setattr(engine, "_run_sandbox_command", fake)
    monkeypatch.setattr(engine, "_AUTO_QA_FAKE_RESULT", "3 passed in 0.12s", raising=False)
    return tmp_path, ran


def _changed(tmp_path):
    state = TrajectoryState({}, goal="add a subtract function", message_count=1, workspace=str(tmp_path))
    (tmp_path / "pkg" / "calc.py").write_text("def sub(a, b):\n    return a - b\n")
    op = state.before_tool("write_file")
    state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(tmp_path / "pkg" / "calc.py"))
    return state


def test_a_passing_suite_verifies_the_change_without_asking(engine, project):
    tmp_path, ran = project
    state = _changed(tmp_path)
    assert engine._auto_qa_step(state) is None
    assert len(ran) == 1 and "python -m pytest -q" in ran[0][0] and str(tmp_path) in ran[0][0]
    assert ran[0][1] == engine.AUTO_QA_SECONDS
    assert state.assurance() == "tested" and not state.needs_verification()


def test_a_failing_suite_sends_the_model_back_with_the_failure(engine, project, monkeypatch):
    tmp_path, ran = project
    monkeypatch.setattr(engine, "_AUTO_QA_FAKE_RESULT",
                        "FAILED tests/test_calc.py::test_add - assert 3 == 2\n1 failed in 0.1s\nCommand exited with status 1.")
    nudge = engine._auto_qa_step(_changed(tmp_path))
    assert nudge is not None and nudge.kind == "auto_qa"
    assert "python -m pytest -q" in nudge.text and "1 failed" in nudge.text


def test_it_runs_once_per_state_of_the_work(engine, project):
    tmp_path, ran = project
    state = _changed(tmp_path)
    state.data["verification"] = "pending"
    engine._auto_qa_step(state)
    state.data["verification"] = "pending"
    engine._auto_qa_step(state)
    assert len(ran) == 1


def test_no_suite_means_the_old_ask(engine, project):
    tmp_path, ran = project
    for f in (tmp_path / "tests").iterdir():
        f.unlink()
    (tmp_path / "tests").rmdir()
    assert engine._auto_qa_step(_changed(tmp_path)) is None
    assert ran == []


def test_a_result_that_says_nothing_leaves_the_old_ask(engine, project, monkeypatch):
    tmp_path, ran = project
    monkeypatch.setattr(engine, "_AUTO_QA_FAKE_RESULT", "Command timed out after 120s.")
    state = _changed(tmp_path)
    assert engine._auto_qa_step(state) is None
    assert state.needs_verification()


@pytest.mark.parametrize("setting", ["off", "nothing_changed", "last_stretch", "no_sandbox"])
def test_when_it_does_not_run(engine, project, monkeypatch, setting):
    tmp_path, ran = project
    state = _changed(tmp_path)
    if setting == "off":
        monkeypatch.setattr(engine, "AUTO_QA", False)
    elif setting == "nothing_changed":
        state.data["verification"] = "verified"
    elif setting == "last_stretch":
        budget = engine._TurnBudget(max_seconds=1000)
        budget.started -= 990
        monkeypatch.setattr(engine, "_active_budget", budget)
    else:
        monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "unavailable")
    assert engine._auto_qa_step(state) is None
    assert ran == []


def test_the_completion_check_runs_it_before_asking(engine, project, monkeypatch):
    tmp_path, ran = project
    state = _changed(tmp_path)
    budget = c.ContinuationBudget(limit=5)
    nudge = engine._next_completion_nudge(budget, state, reply="I added sub().", request="add a subtract function",
                                          depth=0)
    assert ran, "auto QA did not run"
    assert nudge is None or nudge.kind != "verify"
