"""Human think-time must not spend the turn's wall-clock budget.

Plan mode is the only mode with a wall-clock budget on by default
(plan_mode_timeout_seconds, 300s). The plan approval prompt blocks *inside* the
turn, so before this the clock ran while the user read the plan: approving after
a few minutes' read left no budget to execute with, and the turn died reporting
"Turn budget exceeded" - which is what /plan "timing out" looked like.
"""

import time

# Windows' default timer resolution is ~15.6ms, and a sleep can return that
# much *before* an equivalent monotonic delta. These asserts are checking that
# the wait was credited at all, not that the clock is exact, so they carry one
# tick of slack. Without it they fail on ordering alone -- 0.188 >= 0.2 -- and
# whichever test file happens to run first decides the result.
TIMER_SLACK = 0.03


def test_credit_idle_discounts_human_wait(engine):
    budget = engine._TurnBudget(max_seconds=5)
    budget.started = time.monotonic() - 30

    assert budget.exceeded(), "30s against a 5s budget should be over"

    budget.credit_idle(28)

    assert budget.exceeded() is None, "2s of agent work is within a 5s budget"


def test_credit_idle_accumulates(engine):
    budget = engine._TurnBudget(max_seconds=5)
    budget.credit_idle(3)
    budget.credit_idle(4)

    assert budget.idle_seconds == 7


def test_credit_idle_ignores_negative_and_none(engine):
    budget = engine._TurnBudget(max_seconds=5)
    budget.credit_idle(-10)
    budget.credit_idle(None)

    assert budget.idle_seconds == 0


def test_human_wait_credits_the_active_budget(engine, monkeypatch):
    budget = engine._TurnBudget(max_seconds=5)
    monkeypatch.setattr(engine, "_active_budget", budget)

    with engine._human_wait():
        time.sleep(0.2)

    assert budget.idle_seconds >= 0.2 - TIMER_SLACK


def test_human_wait_credits_even_when_the_prompt_raises(engine, monkeypatch):
    budget = engine._TurnBudget(max_seconds=5)
    monkeypatch.setattr(engine, "_active_budget", budget)

    class Boom(Exception):
        pass

    try:
        with engine._human_wait():
            time.sleep(0.1)
            raise Boom()
    except Boom:
        pass

    assert budget.idle_seconds >= 0.1 - TIMER_SLACK


def test_human_wait_is_safe_with_no_active_budget(engine, monkeypatch):
    monkeypatch.setattr(engine, "_active_budget", None)

    with engine._human_wait():
        pass


def test_plan_approval_wait_is_not_charged_to_the_budget(engine, monkeypatch):
    """The end-to-end defect: waiting on the plan prompt must not spend budget."""
    budget = engine._TurnBudget(max_seconds=5)
    monkeypatch.setattr(engine, "_active_budget", budget)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(engine, "PLAN_AUDIT", False)

    def slow_human(plan_text):
        time.sleep(0.2)
        return "full-auto"

    monkeypatch.setattr(engine, "_plan_on_approval", slow_human)

    result = engine._exec_present_plan({"plan": "1. do the thing"})

    assert "Plan approved" in result
    assert budget.idle_seconds >= 0.2 - TIMER_SLACK, "the human's read time was charged to the turn"
