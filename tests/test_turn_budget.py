"""Turn budget: token, cost, and wall-clock ceilings for one run_agent() call.

max_turns bounds how many ROUNDS the loop takes; the budget bounds what those
rounds may consume. Every limit defaults to 0 (disabled), so these tests set
their limits explicitly rather than relying on config.
"""
import pytest

from agent8088 import engine as A


def test_budget_disabled_never_trips():
    b = A._TurnBudget(max_seconds=0, max_tokens=0, max_cost=0.0)
    b.add_tokens(10_000_000, 10_000_000)
    assert b.exceeded() is None


def test_budget_trips_on_tokens():
    b = A._TurnBudget(max_seconds=0, max_tokens=1000, max_cost=0.0)
    b.add_tokens(600, 500)
    reason = b.exceeded()
    assert reason is not None
    assert "token" in reason.lower()
    assert "1100" in reason


def test_budget_under_token_limit_does_not_trip():
    b = A._TurnBudget(max_seconds=0, max_tokens=1000, max_cost=0.0)
    b.add_tokens(400, 500)
    assert b.exceeded() is None


def test_budget_trips_on_wall_clock(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(A.time, "monotonic", lambda: clock["t"])
    b = A._TurnBudget(max_seconds=30, max_tokens=0, max_cost=0.0)
    clock["t"] = 131.0
    reason = b.exceeded()
    assert reason is not None
    assert "second" in reason.lower()


def test_budget_trips_on_cost():
    b = A._TurnBudget(max_seconds=0, max_tokens=0, max_cost=0.05,
                      cost_in=0.01, cost_out=0.03)
    b.add_tokens(1000, 1000)  # 0.01 + 0.03 = 0.04 -> under
    assert b.exceeded() is None
    b.add_tokens(1000, 0)     # +0.01 -> 0.05, at the limit
    assert b.exceeded() is not None


def test_add_usage_reads_openai_shape():
    class _U:
        prompt_tokens = 120
        completion_tokens = 30

    class _R:
        usage = _U()

    b = A._TurnBudget(max_seconds=0, max_tokens=100, max_cost=0.0)
    b.add_usage(_R())
    assert b.exceeded() is not None


def test_add_usage_falls_back_to_estimate_when_usage_missing():
    """Streaming responses built by _build_response carry no usage object."""
    class _R:
        usage = None

    b = A._TurnBudget(max_seconds=0, max_tokens=0, max_cost=0.0)
    b.add_usage(_R(), text="x" * 400)
    assert b.output_tokens == 100  # 400 chars / 4


def test_run_agent_stops_when_budget_exhausted(monkeypatch):
    """A budget already over the limit ends the turn before any model call."""
    calls = []

    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("model must not be called once the budget is spent")

    monkeypatch.setattr(A, "_create_completion_with_fallback", _boom)
    spent = A._TurnBudget(max_seconds=0, max_tokens=100, max_cost=0.0)
    spent.add_tokens(500, 0)

    answers = []
    result = A.run_agent([{"role": "user", "content": "hello"}],
                         max_turns=3, budget=spent,
                         on_answer=answers.append)
    assert calls == []
    assert "budget exceeded" in result.lower()
    assert answers == [result]


def test_run_agent_accumulates_usage_across_turns(monkeypatch, engine):
    """Each model call adds to the budget, so the ceiling bites mid-turn."""
    class _U:
        prompt_tokens = 40
        completion_tokens = 10

    # A distinct tool call each turn, so the loop keeps going (an identical
    # signature would hit run_agent's repeat cache and never re-run).
    counter = {"n": 0}

    def _fake(messages, tools, **kw):
        counter["n"] += 1
        content = ('✿FUNCTION✿: calculate ✿ARGS✿: '
                   '{"expression": "1+%d"}' % counter["n"])
        return type("R", (), {
            "usage": _U(),
            "choices": [type("C", (), {
                "message": type("M", (), {"content": content})(),
                "finish_reason": "stop",
            })()],
        })()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    budget = engine._TurnBudget(max_seconds=0, max_tokens=120, max_cost=0.0)
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=10,
                     budget=budget)
    # 50 tokens per model call, limit 120: calls 1-3 run (150), then the check at
    # the top of the next round trips. The ceiling stops the loop well before
    # max_turns=10 would have.
    assert counter["n"] == 3
    assert budget.total_tokens == 150
    assert budget.exceeded() is not None


def test_active_budget_is_restored_after_the_turn(monkeypatch, engine):
    """The module global must not leak past run_agent, even on an exception."""
    def _boom(*a, **kw):
        raise RuntimeError("backend down")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _boom)
    assert engine._active_budget is None
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1)
    assert engine._active_budget is None


def test_active_budget_is_restored_after_interrupt(monkeypatch, engine):
    def _interrupt():
        return True

    assert engine._active_budget is None
    with pytest.raises(engine.AgentInterrupted):
        engine.run_agent([{"role": "user", "content": "hi"}], max_turns=1,
                         interrupt_check=_interrupt)
    assert engine._active_budget is None


def test_subagent_shares_parent_budget(monkeypatch, engine):
    """A subagent must not start a fresh budget — that would be a free bypass."""
    seen = {}

    def _fake_run_agent(messages, **kw):
        seen["budget"] = kw.get("budget")
        return "done"

    parent = engine._TurnBudget(max_seconds=0, max_tokens=5000, max_cost=0.0)
    monkeypatch.setattr(engine, "_active_budget", parent)
    monkeypatch.setattr(engine, "run_agent", _fake_run_agent)

    agent_type = sorted(engine.SUBAGENT_SPECS)[0]
    engine._exec_subagent({"agent_type": agent_type, "task": "x"}, depth=0)
    assert seen["budget"] is parent
