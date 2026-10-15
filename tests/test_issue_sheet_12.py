"""Issue S12: a reply that hands the work back to the user is not a final answer.

The reported case: the agent ends with "please install Node and run npm start"
and treats that as done. The two look-alikes must stay untouched: genuine
advice (the user asked how to set something up) and a finished answer whose
usage instructions follow real, verified work.
"""
from types import SimpleNamespace as N

import pytest

from agent8088 import completion as c
from agent8088 import trajectory


@pytest.fixture
def eng(engine, tmp_path, monkeypatch):
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    # Keep the direct _next_completion_nudge calls about only the new check.
    engine.COMPLETION_SMOKE_TEST = "off"
    engine.DISPOSABLE_CONTAINER = False
    return engine


HANDS_BACK = [
    "I have prepared everything you asked for. Please install Node and run npm start to see the app.",
    "The project is ready. You need to install Node first, then run npm install and npm start.",
    "All the code is written. You will need to install the dependencies and run the app.",
    "Everything is set up. Install Node.js and then run `npm start`.",
]
NOT_HANDS_BACK = [
    "Done. The app is in app.py and the tests pass. Next steps you could take: add CI.",
    "All tests pass. If you want to see it running, you can run python app.py.",
    "The script is finished. Enter your OpenAI API key in .env and it works.",
]


@pytest.mark.parametrize("reply", HANDS_BACK)
def test_hand_off_phrases_are_detected(reply):
    assert c.hands_work_back(reply), reply


@pytest.mark.parametrize("reply", NOT_HANDS_BACK)
def test_usage_notes_and_user_only_asks_are_not_hand_offs(reply):
    assert not c.hands_work_back(reply), reply


def _state(root):
    # `workspace` stops a project that happens to live under the system temp dir
    # (which pytest's tmp_path does) from being judged all scratch, so a write
    # still arms the verification machinery the way it would in a real run.
    return trajectory.TrajectoryState({}, goal="g", message_count=1, workspace=str(root))


def _write_and_pass(t, path):
    op = t.before_tool("write_file")
    t.after_tool(op, "wrote", failed=False, blocked=False, mutated=True, path=str(path))
    op = t.before_tool("execute_shell")
    t.after_tool(op, "ok", failed=False, blocked=False, mutated=False, verdict="passed")


def test_the_reported_case_gets_a_continuation(eng, tmp_path):
    t = _state(tmp_path)
    budget = c.ContinuationBudget(limit=3)
    reply = ("I have prepared everything you asked for. Please install Node and "
             "run npm start to see the app.")
    nudge = eng._next_completion_nudge(budget, t, reply=reply,
                                       request="Build a web app that shows my sales data.",
                                       depth=0)
    assert nudge is not None and nudge.kind == "hands_back"
    assert "hands the work back" in nudge.unmet
    assert budget.spent == 1, "the nudge draws on the shared completion budget"


def test_a_genuine_advice_reply_is_not_flagged(eng, tmp_path):
    t = _state(tmp_path)
    budget = c.ContinuationBudget(limit=3)
    request = "How do I set up this project on a new machine?"
    reply = "First install Node.js 20, then run npm install and npm start to launch the app."
    assert eng._next_completion_nudge(budget, t, reply=reply, request=request,
                                      depth=0) is None


def test_advice_is_protected_even_when_the_run_has_open_items(eng, tmp_path):
    t = _state(tmp_path)
    op = t.before_tool("execute_shell")
    t.after_tool(op, "boom", failed=True, blocked=False, mutated=False)
    budget = c.ContinuationBudget(limit=3)
    nudge = eng._next_completion_nudge(
        budget, t, reply="You need to install Node first, then run npm start.",
        request="How do I get this running?", depth=0)
    assert nudge is None and budget.spent == 0


def test_a_reply_that_did_the_work_and_describes_results_is_not_flagged(eng, tmp_path):
    target = tmp_path / "app.py"
    target.write_text("print('ok')", encoding="utf-8")
    t = _state(tmp_path)
    _write_and_pass(t, target)
    budget = c.ContinuationBudget(limit=3)
    reply = ("The app is complete: app.py renders the sales chart, and I ran it - "
             "all checks pass. To use it on your machine, install Node and run npm start.")
    assert eng._next_completion_nudge(budget, t, reply=reply,
                                       request="Build a web app that shows my sales data.",
                                       depth=0) is None


def test_a_hands_back_reply_after_the_verify_reminder_is_pushed(eng, tmp_path):
    """The reported escape: the verify nudge is one-shot, and a reply that answers
    it by handing the run to the user ("you must run it yourself") used to end there."""
    target = tmp_path / "app.py"
    target.write_text("print('ok')", encoding="utf-8")
    t = _state(tmp_path)
    op = t.before_tool("write_file")
    t.after_tool(op, "wrote", failed=False, blocked=False, mutated=True, path=str(target))
    budget = c.ContinuationBudget(limit=3)
    reply = "The app is written. You must run python app.py yourself to see it work."
    request = "Build a web app that shows my sales data."
    first = eng._next_completion_nudge(budget, t, reply=reply, request=request, depth=0)
    assert first is not None and first.kind == "verify", "the existing check still fires first"
    second = eng._next_completion_nudge(budget, t, reply=reply, request=request, depth=0)
    assert second is not None and second.kind == "hands_back"


def test_the_loop_pushes_back_once_and_then_accepts(eng, monkeypatch):
    calls, trace = [], []
    replies = ["I have prepared everything. Please install Node and run npm start to see the app.",
               "Done: I installed the dependencies and started the app myself."]

    def model(messages, tools, **kwargs):
        calls.append(messages)
        return N(choices=[N(message=N(content=replies[len(calls) - 1], tool_calls=None),
                              finish_reason="stop")])

    monkeypatch.setattr(eng, "_create_completion_with_fallback", model)
    monkeypatch.setattr(eng, "exec_tool", lambda name, arguments, depth=0: "ok")
    answer = eng.run_agent([{"role": "user", "content": "Build a web app that shows my sales data."}],
                           max_turns=10, trace=trace, dynamic_turns=False)
    assert answer.startswith("Done:")
    assert any(t.get("type") == "completion_check" and t.get("kind") == "hands_back"
               for t in trace), "the reply was not accepted as a final answer"