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
