"""After a check passes, a quiet turn that brings new evidence (a successful call that is not a change and is new to the run) is held:
it does not use up the allowance. The budget for held turns is the ceiling minus the allowance and its two turns of grace, so a run is
still stopped after at most POST_CHECK_CEILING quiet turns. Nothing is held once the wrap-up nudge has been sent, a write starts
over, and with the setting off every quiet turn counts as before."""


def _response(content):
    message = type("M", (), {"content": content, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": message, "finish_reason": "stop"})()]})()


def _calc(i):
    return f'✿FUNCTION✿: calculate ✿ARGS✿: {{"expression": "{i}+1"}}'


def _run(engine, monkeypatch, *, evidence=True, ceiling=15, mutate_at=(), fail_until=-1, cap=5):
    """Call 0 is a tool, call 1 a final-looking reply (the finishing nudge starts the window), every later call another different
    read-only check. Checks up to fail_until return an error; a check whose index is in mutate_at changes state."""
    monkeypatch.setattr(engine, "DISPOSABLE_CONTAINER", True)
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", cap)
    monkeypatch.setattr(engine, "POST_CHECK_NEW_EVIDENCE", evidence)
    monkeypatch.setattr(engine, "POST_CHECK_CEILING", max(cap + 2, ceiling))
    calls = []

    def fake_model(messages, tools, **kwargs):
        calls.append(1)
        i = len(calls) - 1
        if "no more tools will run" in str(messages[-1].get("content")):
            return _response("fresh summary")
        return _response("done" if i == 1 else _calc(i))

    def fake_exec(name, arguments, depth=0):
        index = int(arguments.split('"expression": "')[1].split("+")[0])
        if index in mutate_at:
            engine.note_mutation("test write")
        return "Error: could not compute" if index <= fail_until else "2"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    monkeypatch.setattr(engine, "exec_tool", fake_exec)
    trace = []
    answer = engine.run_agent([{"role": "user", "content": "compute"}], max_turns=60, trace=trace, dynamic_turns=False)
    kinds = [t["type"] for t in trace]
    return answer, len(calls), kinds


def test_new_evidence_is_held_up_to_the_ceiling_and_the_run_still_ends(engine, monkeypatch):
    answer, calls, kinds = _run(engine, monkeypatch)
    assert kinds.count("post_check_held") == 8          # ceiling 15 - (allowance 5 + 2 turns of grace)
    assert kinds.count("post_check_cap") == 1 and "post_check_backstop" in kinds
    assert answer.startswith("fresh summary")
    assert calls == 1 + 1 + 15 + 1                      # a tool, a reply, 15 quiet turns, the wrap-up


def test_the_ceiling_is_a_setting(engine, monkeypatch):
    _answer, calls, kinds = _run(engine, monkeypatch, ceiling=9)
    assert kinds.count("post_check_held") == 2
    assert calls == 1 + 1 + 9 + 1


def test_with_the_setting_off_every_quiet_turn_counts(engine, monkeypatch):
    _answer, calls, kinds = _run(engine, monkeypatch, evidence=False)
    assert "post_check_held" not in kinds
    assert calls == 1 + 1 + 7 + 1                       # the original allowance and grace, as before


def test_a_write_starts_the_window_over(engine, monkeypatch):
    _answer, calls, kinds = _run(engine, monkeypatch, mutate_at=(5,))
    assert kinds.count("post_check_held") == 3 + 8      # three held, the write resets, eight held again
    assert calls == 22


def test_nothing_is_held_once_the_wrap_up_nudge_was_sent(engine, monkeypatch):
    # The first five checks fail (they count), the nudge goes out, and later new reads are not held: the run ends as it always did.
    _answer, calls, kinds = _run(engine, monkeypatch, fail_until=6)
    assert kinds.count("post_check_cap") == 1
    assert "post_check_held" not in kinds
    assert calls == 1 + 1 + 7 + 1


def test_a_failed_check_is_not_new_evidence(engine, monkeypatch):
    _answer, calls, kinds = _run(engine, monkeypatch, fail_until=1000)
    assert "post_check_held" not in kinds
    assert calls == 1 + 1 + 7 + 1


def test_the_new_evidence_setting_is_on_by_default(engine):
    assert engine.POST_CHECK_NEW_EVIDENCE is True
    assert engine.POST_CHECK_CEILING == 15
