"""A run that keeps sending a call that already ran is warned in steps before it is ended, and a cut-off
reply between two repeats does not count towards ending it."""
from types import SimpleNamespace as N

from tests.test_completion_pipeline import _call, eng, failing_then_ok  # noqa: F401

SAME = _call("execute_shell", command="echo same")
OTHER = _call("execute_shell", command="echo other")
REQUEST = [{"role": "user", "content": "run the echo command and report what it prints"}]


def _stop(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _length(content=""):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="length")])


def _script(engine, monkeypatch, replies):
    sent = []

    def completion(messages, *_a, **_k):
        sent.append(str(messages[-1].get("content") or ""))
        reply = replies[min(len(sent) - 1, len(replies) - 1)]
        return reply if not isinstance(reply, str) else _stop(reply)

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "exec_tool", failing_then_ok(["ok"] * 12))
    return sent


def _count(sent, marker):
    """Harness messages that start with the marker (the closing request, the last one sent, repeats them)."""
    return sum(1 for m in sent[:-1] if m.startswith("[agent8088] " + marker))


def _run(engine):
    return engine.run_agent(REQUEST, max_turns=30, tools_def=[], allowed_tools={"execute_shell"})


def test_a_repeated_call_is_warned_three_times_and_the_fourth_ends_the_run(eng, monkeypatch):
    sent = _script(eng, monkeypatch, [SAME] * 8 + ["Gave up."])
    answer = _run(eng)
    assert _count(sent, "You keep repeating tool calls without progress") == 1
    assert _count(sent, "You sent the same call again") == 1
    assert _count(sent, "Last warning") == 1
    assert "stopped because repeated tool calls made no progress" in answer


def test_a_different_call_after_a_warning_clears_it(eng, monkeypatch):
    sent = _script(eng, monkeypatch, [SAME, SAME, SAME, OTHER, SAME, SAME, SAME, "All done."])
    answer = _run(eng)
    assert "stopped because" not in answer
    text = "\n".join(sent)
    assert _count(sent, "You keep repeating tool calls without progress") == 2, "the count started again"
    assert answer.startswith("All done.")


def test_a_cut_off_reply_between_two_repeats_does_not_end_the_run(eng, monkeypatch):
    sent = _script(eng, monkeypatch, [SAME, SAME, _length(""), SAME, SAME, _length(""), SAME, "Finished."])
    answer = _run(eng)
    assert "stopped because" not in answer
    assert answer.startswith("Finished.")


def test_a_sub_agent_still_ends_at_the_second_repeat(eng, monkeypatch):
    sent = _script(eng, monkeypatch, [SAME] * 8 + ["Gave up."])
    answer = eng.run_agent(REQUEST, max_turns=30, tools_def=[], allowed_tools={"execute_shell"}, depth=1)
    text = "\n".join(sent)
    assert _count(sent, "You sent the same call again") == 0
    assert "stopped because repeated tool calls made no progress" in answer
