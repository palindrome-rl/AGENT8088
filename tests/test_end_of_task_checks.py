"""Audit issues 16-23: end-of-task checks stop misreading finished work."""
import pytest

from agent8088 import completion as C
from agent8088.trajectory import TrajectoryState as S


@pytest.mark.parametrize("reply", [
    "Nothing left to do.", "There are no remaining steps.", "I will now explain what changed.",
    "If you haven't installed it yet, run pip install."])
def test_finished_replies_are_not_unfinished(reply):
    assert not C.admits_unfinished_work(reply)


@pytest.mark.parametrize("reply", [
    "I have not yet written the tests.", "Still need to add the CSS.", "Next, I'll write the tests."])
def test_real_admissions_still_count(reply):
    assert C.admits_unfinished_work(reply)


@pytest.mark.parametrize("request_text,advice", [
    ("How do I fix the login bug?", True), ("Explain why this fails", True),
    ("Fix the login bug", False), ("Can you fix the login bug?", False)])
def test_questions_are_advice(request_text, advice):
    assert C.is_advice_request(request_text) is advice


def test_new_request_resets_subgoals_and_smoke_notes():
    s = S(None, "first", 1)
    s.set_subgoals(["part one", "part two"])
    s.record_smoke("a.html", "a.html: broken", True)
    op = s.before_tool("execute_shell")
    s.after_tool(op, "x", failed=True, blocked=False, mutated=False)   # unresolved: state carries over
    nxt = S(s.data, "second", 2)
    assert nxt.open_subgoals() == [] and not nxt.subgoals_built()
    assert nxt.smoke_review() == ""


def test_building_the_checklist_is_not_progress():
    s = S(None, "task")
    before = s.progress_mark()
    s.set_subgoals(["a", "b"])
    assert s.progress_mark() == before
    s.mark_subgoals(["b"])
    assert s.progress_mark() != before


def test_scratch_file_does_not_ask_for_verification():
    s = S(None, "task")
    op = s.before_tool("write_file")
    s.after_tool(op, "Wrote", failed=False, blocked=False, mutated=True, path="scratch/probe.py")
    assert not s.needs_verification()
    op = s.before_tool("write_file")
    s.after_tool(op, "Wrote", failed=False, blocked=False, mutated=True, path="src/app.py")
    assert s.needs_verification()


def test_library_names_are_not_scratch():
    assert not C.is_scratch("src/fetch_utils.py")
    assert C.is_library_code("src/fetch_utils.py")
    assert C.is_scratch("fetch_data.py")


def test_unfinished_parts_are_listed_in_the_answer(engine):
    s = S(None, "task")
    s.set_subgoals(["write the docs", "add the tests"])
    s.mark_subgoals(["add the tests"])
    assert "Not completed: add the tests" in engine._with_task_notes("Done.", s)
