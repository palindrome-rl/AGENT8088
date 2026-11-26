"""One verdict at the top of a finished run that changed something, in four levels.

- NOT VERIFIED: a check ran and failed, or the run shows the work is unfinished (the reasons the corpus of saved runs
  already judges; a file that still does not parse is one more).
- COULD NOT VERIFY: nothing failed and nothing passed because the machine or the sandbox stopped a check from
  running. It names what stopped it.
- VERIFIED: a test run passed after the last change, and no part of the request is waiting for a recheck.
- PARTLY VERIFIED: anything else that changed files: it ran without an error but no test passed, it was only read
  back, or a part was settled before the last change. It says what is missing.

Only the controller's own evidence decides the level; a reply that says "all tests pass" never raises it. NOT
VERIFIED goes above the reply, as it always has; the other three lead the end-of-run notes as one "Verdict:" line, so
the reply stays the first thing a reader sees.
"""
import pytest

from agent8088 import completion as c
from agent8088.trajectory import TrajectoryState

REQUEST = "1. Save the report to out/report.csv\n2. Write unit tests for the totals\n3. Run the script and check it works"


@pytest.fixture
def state(tmp_path):
    s = TrajectoryState({}, goal=REQUEST, message_count=1, workspace=str(tmp_path))
    s.set_subgoals(c.extract_subgoals(REQUEST))
    return s


def _write(s, path):
    op = s.before_tool("write_file")
    s.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(path))


def _test_run(s, passed=True):
    op = s.before_tool("run_tests")
    s.after_tool(op, "3 passed" if passed else "1 failed", failed=not passed, blocked=False, mutated=False,
                 verdict="passed" if passed else "failed", command="pytest -q")


def test_a_tested_run_with_every_part_settled_is_verified(engine, state, tmp_path):
    _write(state, tmp_path / "out" / "report.csv")
    _test_run(state)
    state.mark_subgoals([])
    level, why = engine._verdict(state, "Done.")
    assert level == "VERIFIED"
    answer = engine._with_task_notes("Done.", state)
    assert answer.startswith("Done.")
    assert "\n\nVerdict: VERIFIED (a test run passed after the last change" in answer


def test_a_failed_check_is_not_verified_with_the_usual_headline(engine, state, tmp_path):
    _write(state, tmp_path / "a.py")
    op = state.before_tool("execute_shell")
    state.after_tool(op, "Traceback\nboom\nCommand exited with status 1.", failed=True, blocked=False, mutated=False,
                     verdict="failed", command="python a.py")
    level, _why = engine._verdict(state, "Done.")
    assert level == "NOT VERIFIED"
    assert engine._with_task_notes("Done.", state).startswith("NOT VERIFIED: ")


def test_ran_without_error_but_no_test_is_partly_verified(engine, state, tmp_path):
    _write(state, tmp_path / "out" / "report.csv")
    state.note_ran_ok()
    state.mark_subgoals([])
    level, why = engine._verdict(state, "Done.")
    assert level == "PARTLY VERIFIED" and "nothing tested it" in why


def test_a_part_settled_before_the_last_change_is_partly_verified(engine, state, tmp_path):
    _test_run(state)
    state.mark_subgoals([])
    _write(state, tmp_path / "script.py")
    _test_run(state)            # a test passed after the change: verification is fresh ...
    _write(state, tmp_path / "script.py")
    state.data["verification"] = "verified"   # ... but pretend only the general level is current
    level, why = engine._verdict(state, "Done.")
    assert level == "PARTLY VERIFIED" and "before the last change" in why


def test_an_environment_limit_with_nothing_passed_could_not_verify(engine, state, tmp_path):
    _write(state, tmp_path / "out" / "report.csv")
    state.mark_subgoals([])
    state.data["env_limits"] = [{"reason": "the sandbox could not start the server", "what": "npm start",
                                 "command": "npm start"}]
    level, why = engine._verdict(state, "Done.")
    assert level == "COULD NOT VERIFY" and "could not start the server" in why
    answer = engine._with_task_notes("Done.", state)
    assert answer.startswith("Done.") and "Verdict: COULD NOT VERIFY (" in answer


def test_a_file_that_still_does_not_parse_is_not_verified(engine, state, tmp_path):
    (tmp_path / "a.py").write_text("x = (\n", encoding="utf-8")   # still broken when the verdict checks it again
    _write(state, tmp_path / "a.py")
    _test_run(state)
    state.mark_subgoals([])
    state.note_write_problem(str(tmp_path / "a.py"), "line 3: invalid syntax")
    level, why = engine._verdict(state, "Done.")
    assert level == "NOT VERIFIED" and "a.py" in why


def test_the_model_saying_it_passed_does_not_raise_the_level(engine, state, tmp_path):
    _write(state, tmp_path / "out" / "report.csv")
    state.mark_subgoals([])
    level, _why = engine._verdict(state, "All tests pass and everything is verified.")
    assert level == "PARTLY VERIFIED"


def test_a_run_that_changed_nothing_gets_no_verdict(engine, tmp_path):
    s = TrajectoryState({}, goal="What does this function do?", message_count=1, workspace=str(tmp_path))
    op = s.before_tool("read_text")
    s.after_tool(op, "def f(): ...", failed=False, blocked=False, mutated=False, path=str(tmp_path / "a.py"))
    assert engine._verdict(s, "It returns one.") == ("", "")
    assert engine._with_task_notes("It returns one.", s) == "It returns one."


def test_eval_mode_gets_the_same_verdict(engine, state, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "RUN_MODE", "eval")
    _write(state, tmp_path / "out" / "report.csv")
    _test_run(state)
    state.mark_subgoals([])
    assert "Verdict: VERIFIED (" in engine._with_task_notes("Done.", state)


def test_the_headline_can_still_be_turned_off(engine, state, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "NOT_VERIFIED_HEADLINE", False)
    _write(state, tmp_path / "out" / "report.csv")
    _test_run(state)
    state.mark_subgoals([])
    assert "Verdict:" not in engine._with_task_notes("Done.", state)


def test_the_corpus_reasons_are_unchanged(engine, state, tmp_path):
    """The saved runs are judged by _not_verified_reasons; the new levels sit on top of it and never change it."""
    _write(state, tmp_path / "a.py")
    state.note_write_problem(str(tmp_path / "a.py"), "line 3: invalid syntax")
    assert not any("a.py" in r for r in engine._not_verified_reasons(state, "Done."))


def test_the_auditor_must_cite_evidence_for_a_fail():
    from pathlib import Path
    from agent8088 import engine as e
    text = (Path(e.__file__).parent / "agents" / "auditor.md").read_text(encoding="utf-8")
    assert "cite" in text.lower() and "fail" in text
