"""The model is told when its time or rounds are running out, so the run ends with a working
state and a summary instead of being cut off.

A clock is announced only when one was configured; nothing is announced for a limit nobody set.
"""
from tests.test_completion_pipeline import Script, _call, eng, failing_then_ok, run  # noqa: F401


def test_the_time_message_asks_for_a_summary_only_on_the_last_warning(engine):
    first, last = engine._time_left_nudge(390), engine._time_left_nudge(390, final=True)
    assert "required output exists" in first and "what is done" not in first
    assert "required output exists" in last and "what is done, what is not done" in last


def test_the_rounds_message_names_how_many_are_left(engine):
    assert "about 12 rounds" in engine._rounds_left_nudge(12)
    assert "one round" in engine._rounds_left_nudge(1)
    assert "what you could not verify" in engine._rounds_left_nudge(3, final=True)


def _ran_with_budget(eng, monkeypatch, used: float):
    script = Script(eng, monkeypatch, [_call("execute_shell", command="echo hi"), "Done."],
                    tool=failing_then_ok(["hi"]))
    budget = eng._TurnBudget(max_seconds=100)
    budget.started -= used
    eng.run_agent([{"role": "user", "content": "say hi"}], max_turns=6, tools_def=[],
                  allowed_tools={"execute_shell"}, budget=budget)
    return script


def test_a_configured_clock_is_announced_outside_a_benchmark_container_too(eng, monkeypatch):
    monkeypatch.setattr(eng, "DISPOSABLE_CONTAINER", False)
    script = _ran_with_budget(eng, monkeypatch, used=70)
    assert script.nudges("Time check")
    assert not script.nudges("what is done, what is not done")


def test_the_last_time_warning_asks_for_a_summary(eng, monkeypatch):
    monkeypatch.setattr(eng, "DISPOSABLE_CONTAINER", False)
    script = _ran_with_budget(eng, monkeypatch, used=90)
    assert script.nudges("what is done, what is not done")


def test_no_clock_means_no_time_message(eng, monkeypatch):
    script = Script(eng, monkeypatch, [_call("execute_shell", command="echo hi"), "Done."],
                    tool=failing_then_ok(["hi"]))
    eng.run_agent([{"role": "user", "content": "say hi"}], max_turns=6, tools_def=[],
                  allowed_tools={"execute_shell"}, budget=eng._TurnBudget())
    assert not script.nudges("Time check")


def test_rounds_are_announced_against_the_bound_the_run_cannot_pass(eng, monkeypatch):
    commands = [_call("execute_shell", command=f"echo {i}") for i in range(30)]
    script = Script(eng, monkeypatch, commands + ["Finished."], tool=failing_then_ok([]))
    eng.run_agent([{"role": "user", "content": "count"}], max_turns=24, dynamic_turns=False,
                  tools_def=[], allowed_tools={"execute_shell"})
    notes = script.nudges("Round check")
    assert len(notes) == 2 and "about 4 rounds" in notes[0] and "one round" not in notes[0]
    assert "what is done, what is not done" not in notes[0] and "what is done, what is not done" in notes[1]


def test_short_runs_get_no_round_message(eng, monkeypatch):
    commands = [_call("execute_shell", command=f"echo {i}") for i in range(8)]
    script = Script(eng, monkeypatch, commands + ["Finished."], tool=failing_then_ok([]))
    eng.run_agent([{"role": "user", "content": "count"}], max_turns=10, dynamic_turns=False,
                  tools_def=[], allowed_tools={"execute_shell"})
    assert not script.nudges("Round check")
