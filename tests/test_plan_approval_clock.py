"""Approving a plan ends the planning clock; the build that follows is not cut at plan_mode_timeout_seconds."""

import time


def _budget(engine, monkeypatch, seconds, max_turn_seconds=0):
    budget = engine._TurnBudget(max_seconds=seconds)
    monkeypatch.setattr(engine, "_active_budget", budget)
    monkeypatch.setattr(engine, "MAX_TURN_SECONDS", max_turn_seconds)
    monkeypatch.setattr(engine, "PLAN_MODE_TIMEOUT_SECONDS", 300)
    return budget


def test_approval_lifts_the_plan_mode_ceiling(engine, monkeypatch):
    budget = _budget(engine, monkeypatch, 300)
    budget.started = time.monotonic() - 290          # planning used most of the 300s

    engine._lift_plan_clock()

    assert budget.max_seconds == 0
    budget.started = time.monotonic() - 3000         # an hour of building afterwards
    assert budget.exceeded() is None


def test_unapproved_plan_keeps_its_ceiling(engine, monkeypatch):
    budget = _budget(engine, monkeypatch, 300)
    budget.started = time.monotonic() - 400

    assert budget.exceeded(), "planning past 300s is still stopped"
    assert budget.max_seconds == 300


def test_a_limit_the_user_set_survives_approval(engine, monkeypatch):
    budget = _budget(engine, monkeypatch, 300, max_turn_seconds=300)

    engine._lift_plan_clock()

    assert budget.max_seconds == 300


def test_approval_leaves_another_budget_alone(engine, monkeypatch):
    budget = _budget(engine, monkeypatch, 120)       # e.g. a sub-agent's own limit

    engine._lift_plan_clock()

    assert budget.max_seconds == 120


def test_no_active_budget_is_fine(engine, monkeypatch):
    monkeypatch.setattr(engine, "_active_budget", None)

    engine._lift_plan_clock()


def test_present_plan_approval_lifts_the_clock(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)        # present_plan saves PLAN.md
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    engine.enter_plan_mode()
    budget = _budget(engine, monkeypatch, 300)
    monkeypatch.setattr(engine, "_plan_on_approval", lambda plan: "full-auto", raising=False)
    monkeypatch.setattr(engine, "set_permission_mode", lambda mode: None)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")

    result = engine._exec_present_plan({"plan": "1. write a file"})

    assert "Plan approved" in result
    assert budget.max_seconds == 0
