"""A reply that ends the work while the latest automated check is still failing is sent
back (up to twice per request) with the failing command named."""
import json
from types import SimpleNamespace as N

import pytest

from agent8088 import completion as c
from agent8088.trajectory import TrajectoryState


# --- the state ---------------------------------------------------------------------------

def _record(state, tool, verdict, command="", result="1 failed, 2 passed in 0.1s"):
    op = state.before_tool(tool)
    state.after_tool(op, result, failed=False, blocked=False, mutated=False, verdict=verdict,
                     command=command)


def test_the_failing_command_and_result_are_remembered_until_a_check_passes():
    state = TrajectoryState({}, goal="x", message_count=1)
    _record(state, "execute_shell", "failed", command="pytest -q")
    assert state.check_failing()
    assert "pytest -q" in state.failing_check_detail() and "1 failed" in state.failing_check_detail()
    _record(state, "execute_shell", "passed", command="pytest -q", result="3 passed")
    assert not state.check_failing() and state.failing_check_detail() == ""


def test_each_distinct_failure_is_followed_up_once_and_at_most_twice_a_request():
    state = TrajectoryState({}, goal="x", message_count=1)
    assert not state.request_failing_follow_up()            # nothing failing
    _record(state, "execute_shell", "failed", command="pytest -q", result="1 failed")
    assert state.request_failing_follow_up()
    assert not state.request_failing_follow_up()            # the same failure
    _record(state, "execute_shell", "failed", command="pytest -q", result="2 failed")
    assert state.request_failing_follow_up()                # a different failure
    _record(state, "execute_shell", "failed", command="pytest -q", result="3 failed")
    assert not state.request_failing_follow_up()            # the cap


def test_a_new_request_resets_the_count():
    saved = {}
    state = TrajectoryState(saved, goal="x", message_count=1)
    _record(state, "execute_shell", "failed", command="pytest", result="1 failed")
    assert state.request_failing_follow_up()
    _record(state, "execute_shell", "failed", command="pytest", result="2 failed")
    assert state.request_failing_follow_up()
    again = TrajectoryState(saved, goal="x", message_count=2)
    _record(again, "execute_shell", "failed", command="pytest", result="4 failed")
    assert again.request_failing_follow_up()


# --- the wording --------------------------------------------------------------------------

def test_the_nudge_names_the_failure_and_notices_a_success_claim():
    plain = c.failing_check_nudge("pytest -q -> 1 failed", "I changed the parser.")
    claimed = c.failing_check_nudge("pytest -q -> 1 failed", "Everything works and all tests passed.")
    assert plain.kind == claimed.kind == "failing_check"
    assert "pytest -q -> 1 failed" in plain.text and "Your reply says" not in plain.text
    assert "Your reply says the work is done" in claimed.text
    assert "say plainly" in claimed.text


@pytest.mark.parametrize("reply,expected", [
    ("All tests passed.", True), ("Everything works now.", True), ("The feature is now complete.", True),
    ("Two tests still fail because of the date parser.", False), ("I could not finish this.", False)])
def test_success_claims(reply, expected):
    assert c.claims_success(reply) is expected


# --- through the agent loop -----------------------------------------------------------------

def _reply(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _shell(command):
    return "✿FUNCTION✿: execute_shell ✿ARGS✿: " + json.dumps({"command": command})


@pytest.fixture
def eng(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path
    engine.PLAN_AUDIT = False
    engine.reset_turn_counters()
    monkeypatch.setattr(engine, "FAILING_CHECK_FOLLOW_UP", True)
    monkeypatch.setattr(engine, "COMPLETION_SMOKE_TEST", "off")
    monkeypatch.setattr(engine, "GROUNDING_CHECK", False, raising=False)
    monkeypatch.setattr(engine, "REQUIREMENT_EVIDENCE", False, raising=False)
    return engine


def _run(engine, monkeypatch, script, outputs):
    sent, ran = [], []

    def model(messages, tools, **kwargs):
        sent.append(str(messages[-1].get("content")))
        return _reply(script(len(sent) - 1))

    def tool(name, arguments, depth=0):
        ran.append(arguments)
        return outputs[min(len(ran) - 1, len(outputs) - 1)]

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "exec_tool", tool)
    answer = engine.run_agent([{"role": "user", "content": "Fix the parser so the tests pass."}],
                              max_turns=12, dynamic_turns=False)
    return answer, sent


FAIL = "FAILED tests/test_parser.py::test_dates - assert 1 == 2\n1 failed, 2 passed in 0.12s"
PASS = "3 passed in 0.10s"


def test_a_reply_after_a_failing_test_run_is_sent_back_and_the_fix_is_accepted(eng, monkeypatch):
    def script(i):
        # a different command the second time: an identical repeat is not run again
        return [_shell("python -m pytest -q"), "All tests passed, the parser is fixed.",
                _shell("python -m pytest -q tests"), "The parser is fixed."][min(i, 3)]

    answer, sent = _run(eng, monkeypatch, script, [FAIL, PASS])
    follow = [s for s in sent if "latest automated check failed" in s]
    assert follow and "pytest -q" in follow[0] and "Your reply says the work is done" in follow[0]
    assert "Incomplete" not in answer


def test_the_model_may_disclose_a_failure_it_cannot_fix(eng, monkeypatch):
    def script(i):
        return [_shell("python -m pytest -q"), "Done.", "I could not fix test_dates; it still fails because of the locale."][min(i, 2)]

    answer, sent = _run(eng, monkeypatch, script, [FAIL])
    assert sum("latest automated check failed" in s for s in sent) == 1
    assert "still fails" in answer


def test_with_the_follow_up_off_only_the_note_remains(eng, monkeypatch):
    monkeypatch.setattr(eng, "FAILING_CHECK_FOLLOW_UP", False)

    def script(i):
        return [_shell("python -m pytest -q"), "Done."][min(i, 1)]

    answer, sent = _run(eng, monkeypatch, script, [FAIL])
    assert not any("latest automated check failed" in s for s in sent)
    assert "Incomplete" in answer
