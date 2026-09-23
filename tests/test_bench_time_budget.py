"""A disposable-container run knows its clock: shell calls cannot outlast the
remaining budget, and the model is told when time is running out."""


def test_seconds_left_counts_down_and_is_none_without_a_limit(engine):
    budget = engine._TurnBudget(max_seconds=100)
    budget.started -= 40

    assert 59 <= budget.seconds_left() <= 60
    assert engine._TurnBudget().seconds_left() is None


def test_shell_timeout_is_capped_by_the_remaining_budget(engine, monkeypatch):
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    monkeypatch.setattr(engine, "_active_budget", engine._TurnBudget(max_seconds=100))
    engine._active_budget.started -= 50          # ~50 s left

    assert 19 <= engine._budget_capped_timeout(600) <= 20
    assert engine._budget_capped_timeout(5) == 5  # short calls untouched

    engine._active_budget.started -= 100         # overrun: never zero or negative
    assert engine._budget_capped_timeout(600) == 1


def test_shell_timeout_is_untouched_outside_a_disposable_container(engine, monkeypatch):
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(engine, "_active_budget", engine._TurnBudget(max_seconds=31))

    assert engine._budget_capped_timeout(600) == 600
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    monkeypatch.setattr(engine, "_active_budget", engine._TurnBudget())  # no time limit
    assert engine._budget_capped_timeout(600) == 600


def test_time_left_nudge_names_the_time_and_the_priority(engine):
    assert "about 6 min" in engine._time_left_nudge(390)
    assert "under a minute" in engine._time_left_nudge(20)
    assert "required output exists" in engine._time_left_nudge(390)
