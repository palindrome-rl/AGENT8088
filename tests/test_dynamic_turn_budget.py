"""max_turns is a starting budget, not a verdict.

A run that keeps producing new, successful tool results has not failed — it is
working — so ending it on a number the user guessed before the task began
throws away real work and reports it as an error. These tests pin the deal:
progress buys more rounds, up to a ceiling derived from max_turns; anything
else (a repeat, a failed step, a stalled model) buys nothing; and when the
ceiling is finally reached the run answers from what it has instead of
returning a raw error and a tool dump.
"""
import json

import pytest


def _tool_call_response(name, args):
    call = type("T", (), {
        "function": type("F", (), {"name": name, "arguments": json.dumps(args)})(),
    })()
    message = type("M", (), {"content": "", "tool_calls": [call]})()
    choice = type("C", (), {"message": message, "finish_reason": "tool_calls"})()
    return type("R", (), {"choices": [choice]})()


def _text_response(text):
    message = type("M", (), {"content": text, "tool_calls": []})()
    choice = type("C", (), {"message": message, "finish_reason": "stop"})()
    return type("R", (), {"choices": [choice]})()


class _Model:
    """A model that never volunteers an answer: every round is a fresh call.

    Distinct arguments each round keep the repeat guard out of the way, so what
    these tests observe is the turn policy and nothing else.
    """

    def __init__(self, answer_when_toolless="done, here is what I found"):
        self.rounds = 0
        self.toolless_rounds = 0
        self.answer_when_toolless = answer_when_toolless

    def __call__(self, messages, tools, **kwargs):
        self.rounds += 1
        if not tools:
            self.toolless_rounds += 1
            if self.answer_when_toolless is None:
                raise RuntimeError("model refused the wrap-up round")
            return _text_response(self.answer_when_toolless)
        return _tool_call_response("execute_shell", {"command": f"echo {self.rounds}"})


@pytest.fixture
def loop(monkeypatch, engine):
    """Wire a fresh engine to a scripted model and a counting tool runner."""

    def _wire(*, tool_result="ok", answer_when_toolless="done, here is what I found"):
        model = _Model(answer_when_toolless=answer_when_toolless)
        executed = []
        monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
        monkeypatch.setattr(engine, "run_tool",
                            lambda name, args, **kw: executed.append(name) or tool_result)
        monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
        return model, executed

    return _wire


def test_a_progressing_run_keeps_going_past_max_turns(loop, engine):
    """New tool results each round: the run must not stop at the soft limit."""
    _, executed = loop()

    engine.run_agent([{"role": "user", "content": "do the multi-step thing"}],
                     max_turns=4)

    assert len(executed) > 4, (
        f"run stopped at {len(executed)} rounds while still making progress")


def test_extension_stops_at_the_ceiling_derived_from_max_turns(loop, engine):
    """Progress buys rounds, but not without end."""
    monkeypatch_multiplier(engine, 4)
    _, executed = loop()

    engine.run_agent([{"role": "user", "content": "do the endless thing"}],
                     max_turns=4)

    assert len(executed) == 16, (
        f"ceiling should be 4 x max_turns = 16 rounds, got {len(executed)}")


def test_a_failed_tool_result_buys_no_extension(loop, engine):
    """A step that did not do what was asked is not progress."""
    _, executed = loop(tool_result="Error: command not found")

    engine.run_agent([{"role": "user", "content": "do the broken thing"}],
                     max_turns=4)

    assert len(executed) == 4, (
        f"a failing run extended to {len(executed)} rounds; it should stop at 4")


def test_a_multiplier_of_one_reproduces_the_old_fixed_limit(loop, engine):
    """The opt-out convention used elsewhere in the file: 1 disables growth."""
    monkeypatch_multiplier(engine, 1)
    _, executed = loop()

    engine.run_agent([{"role": "user", "content": "do the thing"}], max_turns=4)

    assert len(executed) == 4, (
        f"multiplier 1 must not extend; ran {len(executed)} rounds")


def test_each_grant_is_recorded_in_the_trace(loop, engine):
    """An invisible budget change is one nobody can debug or cost out."""
    loop()
    trace = []

    engine.run_agent([{"role": "user", "content": "do the thing"}],
                     max_turns=4, trace=trace)

    grants = [step for step in trace if step.get("type") == "turn_extension"]
    assert grants, "no turn_extension entries in the trace"
    assert grants[0]["from"] == 4 and grants[0]["to"] > 4


def test_exhaustion_answers_from_what_it_has(loop, engine):
    """Reaching the ceiling must not throw away the work that got there."""
    model, _ = loop()

    answer = engine.run_agent([{"role": "user", "content": "do the endless thing"}],
                              max_turns=4)

    assert model.toolless_rounds == 1, (
        f"expected exactly one tool-free wrap-up round, got {model.toolless_rounds}")
    assert "here is what I found" in answer
    assert not answer.startswith("Error: Agent")


def test_a_wrap_up_that_fails_falls_back_to_the_limit_error(loop, engine):
    """The wrap-up is a best effort; its failure must not lose the old report."""
    loop(answer_when_toolless=None)

    answer = engine.run_agent([{"role": "user", "content": "do the endless thing"}],
                              max_turns=4)

    assert answer.startswith("Error: Agent"), answer


def test_a_stalled_model_still_stops_early(monkeypatch, engine):
    """Extension must not resurrect the runs the stall detector exists to kill."""
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **k: _tool_call_response("execute_shell",
                                                            {"command": "echo hi"}))
    executed = []
    monkeypatch.setattr(engine, "run_tool",
                        lambda name, args, **kw: executed.append(name) or "hi")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")

    engine.run_agent([{"role": "user", "content": "run echo hi"}], max_turns=8)

    assert len(executed) <= 3, (
        f"identical call executed {len(executed)} times before the loop broke out")


# --- scope: who is allowed to grow -----------------------------------------

def monkeypatch_multiplier(engine, value):
    engine.DYNAMIC_TURNS_CEILING_MULTIPLIER = value


def test_the_top_level_run_and_subagents_may_grow(engine):
    top = engine._turn_policy(4, depth=0, role=None)
    sub = engine._turn_policy(8, depth=1, role="subagent:researcher")

    assert top.ceiling > top.limit
    assert sub.ceiling > sub.limit


def test_the_auditor_and_deeper_nesting_stay_fixed(engine):
    auditor = engine._turn_policy(8, depth=1, role="subagent:auditor")
    deep = engine._turn_policy(8, depth=2, role="subagent:researcher")

    assert auditor.ceiling == auditor.limit
    assert deep.ceiling == deep.limit


def test_a_single_turn_call_never_grows(engine):
    """The web server's classification call asks for exactly one round."""
    policy = engine._turn_policy(1, depth=0, role=None)

    assert policy.ceiling == 1


def test_a_caller_can_opt_out_of_growth(loop, engine):
    """Durable tasks already re-run the agent per slice; growth would compound."""
    _, executed = loop()

    engine.run_agent([{"role": "user", "content": "do the thing"}],
                     max_turns=4, dynamic_turns=False)

    assert len(executed) == 4, (
        f"dynamic_turns=False still extended to {len(executed)} rounds")
