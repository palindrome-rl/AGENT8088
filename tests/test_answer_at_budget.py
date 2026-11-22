"""The end of a time budget: a reply near the end is taken as the answer, and a run the budget stops still says what its
own checks found.

Seen in benchmark runs: the server the run wrote passed every grader check, but a completion check sent the model back to
work with half a minute left, the budget then stopped the run, and the user got the budget message and the last tool's
output, with no verification level and no notes."""
from tests.test_completion_pipeline import (  # noqa: F401
    Script,
    _call,
    eng,
    failing_then_ok,
)

PYTEST = _call("execute_shell", command="python -m pytest -q")
PASSED = "3 passed in 0.2s"


def _run(eng, script, *, used, max_seconds=900, request="fix the server and say what you changed"):
    budget = eng._TurnBudget(max_seconds=max_seconds)
    budget.started -= used
    trace = []
    answer = eng.run_agent([{"role": "user", "content": request}], max_turns=8, tools_def=[],
                           allowed_tools={"execute_shell", "write_file"}, budget=budget, trace=trace)
    return answer, trace


def test_answer_time_is_the_last_stretch_of_a_clock(engine):
    clock = engine._TurnBudget(max_seconds=900)
    assert not engine._answer_time(clock)
    clock.started -= 820
    assert engine._answer_time(clock)
    long = engine._TurnBudget(max_seconds=7200)
    long.started -= 6700
    assert not engine._answer_time(long)          # 500 s left of two hours is still time to act
    long.started -= 200
    assert engine._answer_time(long)              # 5% of two hours is 360 s
    assert not engine._answer_time(engine._TurnBudget())
    assert not engine._answer_time(None)


def test_near_the_end_a_reply_is_taken_and_the_unmet_check_is_noted(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="server.py", content="print('x')"),
        "I changed server.py.",
        "Still working on it.",
    ], tool=failing_then_ok(["ok"]))
    answer, trace = _run(eng, script, used=850)
    assert answer.startswith("NOT VERIFIED") or "Verification note" in answer
    assert "I changed server.py." in answer
    assert not [s for s in trace if s.get("type") == "completion_check"]


def test_with_time_left_the_completion_checks_still_run(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="server.py", content="print('x')"),
        "I changed server.py.",
        _call("execute_shell", command="python -m pytest -q"),
        "Tests pass.",
    ], tool=failing_then_ok(["ok", PASSED]))
    _answer, trace = _run(eng, script, used=10)
    assert [s for s in trace if s.get("type") == "completion_check"]


def test_a_budget_stop_carries_the_verification_level(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="server.py", content="print('x')"),
        PYTEST,
        _call("execute_shell", command="echo still going"),
    ], tool=failing_then_ok(["ok", PASSED, "still going"]))
    calls = {"n": 0}
    real_exceeded = eng._TurnBudget.exceeded

    def exceeded(self):
        calls["n"] += 1
        return "Time budget exceeded: 901 seconds elapsed (limit 900s)." if calls["n"] > 3 else real_exceeded(self)

    monkeypatch.setattr(eng._TurnBudget, "exceeded", exceeded)
    answer, trace = _run(eng, script, used=0)
    assert answer.startswith("Time budget exceeded") or "Time budget exceeded" in answer
    assert "stopped before the model gave a final answer" in answer
    assert "Verification: tested" in answer
    assert [s for s in trace if s.get("type") == "budget_exceeded"]


def test_a_budget_stop_with_unchecked_changes_says_so(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("write_file", filename="server.py", content="print('x')"),
        _call("execute_shell", command="echo still going"),
    ], tool=failing_then_ok(["ok", "still going"]))
    calls = {"n": 0}
    real_exceeded = eng._TurnBudget.exceeded

    def exceeded(self):
        calls["n"] += 1
        return "Time budget exceeded: 901 seconds elapsed (limit 900s)." if calls["n"] > 2 else real_exceeded(self)

    monkeypatch.setattr(eng._TurnBudget, "exceeded", exceeded)
    answer, _trace = _run(eng, script, used=0)
    assert "no fresh automated verification" in answer or answer.startswith("NOT VERIFIED")


def test_the_last_stretch_covers_one_more_turn_at_the_run_pace(engine):
    # Seen on a slow model: turns took 60 to 200 s, and with a fixed 90 s the answer ask came too late to be answered.
    clock = engine._TurnBudget(max_seconds=1485)
    clock.started -= 1485 - 146
    assert not engine._answer_time(clock)                     # 146 s left is outside the fixed stretch
    assert engine._answer_time(clock, turn_seconds=100)       # but one more 100 s turn would not fit
    assert not engine._answer_time(clock, turn_seconds=60)    # a 60 s turn still fits
    early = engine._TurnBudget(max_seconds=1485)
    early.started -= 1485 - 400
    assert not engine._answer_time(early, turn_seconds=1000)  # capped at a fifth of the clock (297 s)
