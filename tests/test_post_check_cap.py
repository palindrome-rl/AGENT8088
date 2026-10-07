"""After a finishing nudge fires, read-only check rounds are capped: the model is
told to answer after max_post_check_rounds, and the best answer so far is
returned two rounds later. A write resets the count; 0 disables the cap."""


def _response(content):
    message = type("M", (), {"content": content, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": message,
                                                     "finish_reason": "stop"})()]})()


def _calc(i):
    return f'✿FUNCTION✿: calculate ✿ARGS✿: {{"expression": "{i}+1"}}'


def _run(engine, monkeypatch, script, *, mutate_at=(), cap=5):
    """script(call_index, messages) -> reply text. Round i of calculate
    mutates state when i is in mutate_at."""
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", cap)
    calls = []

    def fake_model(messages, tools, **kwargs):
        calls.append([m.get("content") for m in messages])
        return _response(script(len(calls) - 1, messages))

    def fake_exec(name, arguments, depth=0):
        if any(f'"{i}+1"' in arguments for i in mutate_at):
            engine.note_mutation("test write")
        return "2"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    monkeypatch.setattr(engine, "exec_tool", fake_exec)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "compute"}], max_turns=40,
                              trace=trace, dynamic_turns=False)
    return answer, calls, [t["type"] for t in trace]


def _keeps_checking(i, _messages):
    # call 0: a tool; call 1: a final-looking reply (deliverables nudge fires);
    # every later call: another read-only check, forever.
    return "done" if i == 1 else _calc(i)


def test_read_only_checks_hit_the_backstop_with_the_best_answer(engine, monkeypatch):
    answer, calls, kinds = _run(engine, monkeypatch, _keeps_checking)
    assert kinds.count("post_check_cap") == 1
    assert "post_check_backstop" in kinds
    assert answer.startswith("done")
    # 1 tool + 1 reply + (5 + 2) check rounds
    assert len(calls) == 9


def test_model_that_obeys_the_cap_nudge_answers_without_more_gates(engine, monkeypatch):
    def script(i, messages):
        if any(engine.POST_CHECK_CAP_NUDGE in str(m.get("content")) for m in messages):
            return "final answer"
        return _keeps_checking(i, messages)

    answer, _calls, kinds = _run(engine, monkeypatch, script)
    assert answer.startswith("final answer")
    assert "post_check_cap" in kinds and "post_check_backstop" not in kinds


def test_a_write_after_the_nudge_resets_the_count(engine, monkeypatch):
    # check round 3 (call index 4) writes; the cap then needs 5 more clean rounds.
    _answer, calls, kinds = _run(engine, monkeypatch, _keeps_checking, mutate_at=(4,))
    assert "post_check_backstop" in kinds
    assert len(calls) == 9 + 3


def test_zero_disables_the_cap(engine, monkeypatch):
    def script(i, messages):
        return "final" if i >= 12 else _keeps_checking(i, messages)

    answer, _calls, kinds = _run(engine, monkeypatch, script, cap=0)
    assert answer.startswith("final")
    assert "post_check_cap" not in kinds and "post_check_backstop" not in kinds


def test_structured_git_writes_count_as_state_changes(engine, monkeypatch):
    monkeypatch.setattr(engine, "_structured_tool_argv", lambda name, args: ["git"])
    monkeypatch.setattr(engine, "_exec_structured_tool", lambda name, args, timeout: "ok")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    before = engine._MUTATION_SEQ
    engine.run_tool("git_status", {})
    assert engine._MUTATION_SEQ == before
    engine.run_tool("git_commit", {"message": "fix"})
    assert engine._MUTATION_SEQ > before
