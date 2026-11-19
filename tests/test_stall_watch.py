"""A run that brings nothing new is asked to decide, then ended with a summary; a run that is built
and passing is told that finishing is the next step. Reading many new files is not a stall."""
import json

from tests.test_completion_pipeline import Script, _call, eng, failing_then_ok, run  # noqa: F401


def _watch(engine, churn=3, settled=4):
    return engine._StallWatch(churn, settled)


def _round(watch, productive=False, mark=(0, 0, 0), build=(0, 0), settled=False):
    return watch.record(productive=productive, mark=mark, build_mark=build, settled=settled)


def test_rounds_without_anything_new_ask_for_a_decision_then_end_the_run(engine):
    watch = _watch(engine)
    said = [_round(watch) for _ in range(7)]
    assert said == [None, None, "churn", None, None, "churn_stop", "churn_stop"]


def test_any_progress_resets_the_count_and_the_warning(engine):
    watch = _watch(engine)
    assert [_round(watch) for _ in range(3)][-1] == "churn"
    assert _round(watch, productive=True) is None
    assert [_round(watch) for _ in range(3)][-1] == "churn"       # warned again after new progress
    assert _round(watch, mark=(0, 1, 0)) is None                  # a check newly passing is progress too


def test_a_built_and_passing_run_that_changes_nothing_is_told_to_finish_once_per_stretch(engine):
    watch = _watch(engine, churn=50, settled=4)
    said = [_round(watch, productive=True, settled=True) for _ in range(5)]
    assert said == [None, None, None, "settled", None]            # productive rounds, but no file changed
    assert [_round(watch, productive=True, settled=True) for _ in range(3)][-1] == "settled"
    assert [_round(watch, productive=True, settled=True) for _ in range(9)].count("settled") == 0, "at most twice"


def test_settled_needs_the_work_to_be_built_and_passing(engine):
    watch = _watch(engine, churn=50, settled=3)
    assert [_round(watch, productive=True, settled=False) for _ in range(6)] == [None] * 6
    assert _round(watch, productive=True, build=(1, 0), settled=True) is None   # a file changed: counting restarts


def test_both_checks_can_be_switched_off(engine):
    watch = engine._StallWatch(0, 0)
    assert [_round(watch, settled=True) for _ in range(30)] == [None] * 30


def test_a_run_whose_calls_all_fail_is_asked_to_decide_and_then_ends_with_a_summary(eng, monkeypatch):
    monkeypatch.setattr(eng, "STALL_ROUNDS", 3)
    commands = [_call("execute_shell", command=f"try {i}") for i in range(10)]
    script = Script(eng, monkeypatch, commands + ["Nothing worked."], tool=failing_then_ok(["Error: nope"] * 10))
    answer = eng.run_agent([{"role": "user", "content": "make it work"}], max_turns=30,
                           tools_def=[], allowed_tools={"execute_shell"})
    assert len(script.nudges("Nothing new has come out of your last 3 rounds")) == 1
    assert "stopped because repeated tool calls made no progress" in answer
    assert script.calls < 10, "the run ended well before the commands ran out"


def test_reading_many_new_things_is_not_a_stall(eng, monkeypatch):
    monkeypatch.setattr(eng, "STALL_ROUNDS", 3)
    commands = [_call("execute_shell", command=f"cat file{i}.txt") for i in range(9)]
    script = Script(eng, monkeypatch, commands + ["Read them all."], tool=failing_then_ok(["text"] * 9))
    answer = eng.run_agent([{"role": "user", "content": "read the files"}], max_turns=30,
                           tools_def=[], allowed_tools={"execute_shell"})
    assert not script.nudges("Nothing new has come out")
    assert answer.startswith("Read them all.")


def test_a_passing_built_run_that_keeps_looking_is_told_to_finish(eng, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "VERIFICATION_LOOP_ROUNDS", 3)
    target = str(tmp_path / "app.py")
    replies = ([_call("write_file", filename=target, content="x = 1\n"),
                _call("execute_shell", command="pytest -q")]
               + [_call("execute_shell", command=f"cat note{i}.txt") for i in range(4)] + ["All done."])
    script = Script(eng, monkeypatch, replies, tool=failing_then_ok(["wrote", "1 passed in 0.01s"] + ["text"] * 4))
    eng.run_agent([{"role": "user", "content": "write app.py"}], max_turns=30,
                  tools_def=[], allowed_tools={"write_file", "execute_shell"})
    assert len(script.nudges("the latest checks pass")) == 1


def test_explicit_waits_do_not_count_as_churn(eng, monkeypatch):
    monkeypatch.setattr(eng, "STALL_ROUNDS", 3)
    replies = [_call("execute_shell", command="sleep 1") for _ in range(9)] + ["Finished waiting."]
    script = Script(eng, monkeypatch, replies, tool=failing_then_ok(["waited"] * 9))
    trace = []
    answer = eng.run_agent([{"role": "user", "content": "Wait for nine polling rounds."}],
                           max_turns=30, tools_def=[], allowed_tools={"execute_shell"}, trace=trace)
    assert not any(t.get("type") == "stall_stop" for t in trace)
    assert answer.startswith("Finished waiting.")


def test_successful_new_read_is_progress_even_if_last_call_in_round_fails(eng, monkeypatch):
    monkeypatch.setattr(eng, "STALL_ROUNDS", 2)
    replies = [(_call("execute_shell", command=f"cat note{i}.txt") + "\n"
                + _call("execute_shell", command=f"broken{i}")) for i in range(7)] + ["Read all notes."]
    script = Script(eng, monkeypatch, replies, tool=failing_then_ok(["text", "Error: unavailable"] * 7))
    trace = []
    answer = eng.run_agent([{"role": "user", "content": "Read the notes and try the optional tools."}],
                           max_turns=30, tools_def=[], allowed_tools={"execute_shell"}, trace=trace)
    assert not any(t.get("type") == "stall_stop" for t in trace)
    assert answer.startswith("Read all notes.")


def _cut_off_aware_tool(eng):
    """A tool that answers a call whose arguments never parsed the way the real one does."""
    def tool(name, arguments, depth=0):
        args = json.loads(arguments)
        if args.get("__parse_error__"):
            return eng._tool_arg_parse_error(name, str(args["__parse_error__"]))
        return "ok"
    return tool


CUT = '✿FUNCTION✿: write_file ✿ARGS✿: {"filename": "a.txt", "content": "abc'


def test_cut_off_calls_spread_over_a_long_run_do_not_force_an_early_final_answer(eng, monkeypatch):
    replies = []
    for i in range(3):                       # two cut-off calls, then a call that works, three times
        replies += [CUT, CUT, _call("execute_shell", command=f"echo {i}")]
    replies += ["All finished."]
    script = Script(eng, monkeypatch, replies, tool=_cut_off_aware_tool(eng))
    answer = eng.run_agent([{"role": "user", "content": "do the work"}], max_turns=40,
                           tools_def=[], allowed_tools={"write_file", "execute_shell"})
    assert not script.nudges("You keep repeating tool calls without progress")
    assert answer.startswith("All finished.")


def test_cut_off_calls_in_a_row_still_end_in_a_forced_answer(eng, monkeypatch):
    script = Script(eng, monkeypatch, [CUT] * 12 + ["Gave up."], tool=_cut_off_aware_tool(eng))
    eng.run_agent([{"role": "user", "content": "do the work"}], max_turns=40,
                  tools_def=[], allowed_tools={"write_file", "execute_shell"})
    assert script.nudges("You keep repeating tool calls without progress")


def test_empty_replies_spread_over_a_run_each_get_their_one_retry(eng, monkeypatch):
    replies = []
    for i in range(3):
        replies += ["", _call("execute_shell", command=f"echo {i}")]
    replies += ["All finished."]
    script = Script(eng, monkeypatch, replies, tool=failing_then_ok(["ok"] * 3))
    answer = eng.run_agent([{"role": "user", "content": "do the work"}], max_turns=40,
                           tools_def=[], allowed_tools={"execute_shell"})
    assert len(script.nudges("You did not provide an answer")) == 3
    assert answer.startswith("All finished.") and "wasn't able to produce" not in answer


def test_unknown_tool_calls_spread_over_a_run_are_each_corrected(eng, monkeypatch):
    replies = []
    for i in range(3):
        replies += [_call("no_such_tool", x=i), _call("execute_shell", command=f"echo {i}")]
    replies += ["All finished."]
    script = Script(eng, monkeypatch, replies, tool=failing_then_ok(["ok"] * 3))
    eng.run_agent([{"role": "user", "content": "do the work"}], max_turns=40,
                  tools_def=[], allowed_tools={"execute_shell"})
    assert len(script.nudges("does not exist")) == 3


def test_a_long_run_with_trouble_spread_through_it_is_never_forced_to_stop(eng, monkeypatch):
    """Sixty rounds with a cut-off call or an unknown tool every few rounds."""
    replies = []
    for i in range(60):
        if i % 12 == 3:
            replies.append(CUT)
        elif i % 12 == 10:
            replies.append(_call("no_such_tool", x=i))
        replies.append(_call("execute_shell", command=f"echo {i}"))
    replies.append("All finished.")
    script = Script(eng, monkeypatch, replies, tool=_cut_off_aware_tool(eng))
    answer = eng.run_agent([{"role": "user", "content": "do the work"}], max_turns=200,
                           tools_def=[], allowed_tools={"execute_shell", "write_file"})
    assert not script.nudges("You keep repeating tool calls without progress")
    assert answer.startswith("All finished.")
