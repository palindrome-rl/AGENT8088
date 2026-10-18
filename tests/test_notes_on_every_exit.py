"""Every way a run can finish says what the run's own checks found.

Seen in real runs: a run ended by repeated output cut-offs, or by the repeated-call breaker, gave an honest model reply but
no verification level and no "NOT VERIFIED" headline, because only the ordinary finish path added the end-of-run notes."""
from types import SimpleNamespace as N

from tests.test_completion_pipeline import _call, eng, failing_then_ok  # noqa: F401
from tests.test_length_retry import _PARTIAL_CALL, _length_response, _stop_response

PYTEST = _call("execute_shell", command="python -m pytest -q")
SAME = _call("execute_shell", command="echo same")
TESTED = {"mutation_count": 2, "verification": "verified", "passed_checks": 1}
REQUEST = [{"role": "user", "content": "run the echo command and report what it prints"}]
FAILED_RUN = "FAILED (failures=1)\nCommand exited with status 1."


def _stop(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def test_a_run_ended_by_repeated_cutoffs_carries_the_headline(monkeypatch, eng):
    monkeypatch.setattr(eng, "LENGTH_CUTOFF_MAX_RETRIES", 4)
    monkeypatch.setattr(eng, "compact_messages", lambda *a, **k: False)
    monkeypatch.setattr(eng, "exec_tool", failing_then_ok([FAILED_RUN]))
    sent = []

    def fake(messages, tools, max_tokens=None, **kw):
        sent.append(tools)
        if tools == []:
            return _stop_response("partial work")
        return _stop_response(PYTEST) if len(sent) == 1 else _length_response(_PARTIAL_CALL)

    monkeypatch.setattr(eng, "_create_completion_with_fallback", fake)
    result = eng.run_agent(REQUEST, max_turns=10, allowed_tools={"execute_shell"})
    assert "main_llm_min_tokens" in result, result[:300]
    assert result.startswith("NOT VERIFIED:") and "partial work" in result


def test_a_run_ended_by_the_repeat_breaker_carries_the_headline(eng, monkeypatch):
    sent = []

    def scripted(messages, *_a, **_k):
        sent.append(str(messages[-1].get("content") or ""))
        reply = PYTEST if len(sent) == 1 else SAME if len(sent) <= 9 else "Gave up."
        return _stop(reply)

    monkeypatch.setattr(eng, "_create_completion_with_fallback", scripted)
    monkeypatch.setattr(eng, "exec_tool", failing_then_ok([FAILED_RUN]))
    answer = eng.run_agent(REQUEST, max_turns=30, tools_def=[], allowed_tools={"execute_shell"})
    assert "stopped because repeated tool calls made no progress" in answer, answer[:300]
    assert answer.startswith("NOT VERIFIED:")


def test_the_ordinary_finish_is_unchanged(eng, monkeypatch):
    monkeypatch.setattr(eng, "_create_completion_with_fallback",
                        lambda *a, **k: N(choices=[N(message=N(content="All done.", tool_calls=None),
                                                     finish_reason="stop")]))
    answer = eng.run_agent(REQUEST, max_turns=5, tools_def=[], allowed_tools=set())
    assert answer.startswith("All done.") and "NOT VERIFIED" not in answer


def test_tested_says_what_it_covers(engine):
    from agent8088.trajectory import TrajectoryState
    state = TrajectoryState({}, goal="g", message_count=1)
    state.data.update(TESTED)
    note = engine._with_task_notes("Done.", state)
    assert "the run's own tests" in note and "does not check the result against what was asked" in note
