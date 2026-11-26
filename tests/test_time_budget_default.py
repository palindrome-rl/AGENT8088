"""A headless run has a clock by default, and a run the clock stops says what it got done.

Four of six long build runs reached their outer limit with no final answer, and whatever was done was reported nowhere.
A run with nobody watching (--prompt-file, an unattended job, a task container) now gets
headless_max_turn_seconds (3 hours) when no max_turn_seconds is set, so the time checks and the answer stretch work.
An explicit max_turn_seconds always wins, 0 for headless_max_turn_seconds turns the default off, and the interactive
REPL keeps no clock. When the clock stops a run before the model answered, the answer lists what is done (with its
evidence), what is not, the files changed and the latest check, from the ledger, with no model call.
"""
from pathlib import Path

import pytest

from agent8088 import completion as c
from agent8088.trajectory import TrajectoryState
from tests.test_completion_pipeline import Script, eng  # noqa: F401


def _run(eng, monkeypatch):
    Script(eng, monkeypatch, ["Done."])
    eng.run_agent([{"role": "user", "content": "say hi"}], max_turns=3, tools_def=[], allowed_tools=set())
    return eng._outer_turn_budget.max_seconds


@pytest.mark.parametrize("headless, configured, default, expected", [
    (True, 0, 10800, 10800),      # nobody watching, no clock set: the default
    (True, 600, 10800, 600),      # an explicit clock wins
    (True, 0, 0, 0),              # the default turned off
    (False, 0, 10800, 0),         # the REPL keeps no clock
])
def test_the_clock_for_a_headless_run(eng, monkeypatch, headless, configured, default, expected):
    monkeypatch.setattr(eng, "HEADLESS_RUN", headless)
    monkeypatch.setattr(eng, "UNATTENDED", False)
    monkeypatch.setattr(eng, "DISPOSABLE_CONTAINER", False)
    monkeypatch.setattr(eng, "MAX_TURN_SECONDS", configured)
    monkeypatch.setattr(eng, "HEADLESS_MAX_TURN_SECONDS", default)
    assert _run(eng, monkeypatch) == expected


def test_an_unattended_job_counts_as_headless(eng, monkeypatch):
    monkeypatch.setattr(eng, "HEADLESS_RUN", False)
    monkeypatch.setattr(eng, "UNATTENDED", True)
    monkeypatch.setattr(eng, "MAX_TURN_SECONDS", 0)
    monkeypatch.setattr(eng, "HEADLESS_MAX_TURN_SECONDS", 10800)
    assert _run(eng, monkeypatch) == 10800


def test_the_prompt_file_mode_marks_the_run_headless():
    from agent8088 import cli
    source = Path(cli.__file__).read_text(encoding="utf-8")
    start = source.index("def _run_prompt_file")
    assert "A.HEADLESS_RUN = True" in source[start:start + 4000]


def test_a_run_the_clock_stopped_says_what_it_got_done(engine, tmp_path):
    request = "1. Save the report to out/report.csv\n2. Write unit tests for the totals"
    state = TrajectoryState({}, goal=request, message_count=1, workspace=str(tmp_path))
    state.set_subgoals(c.extract_subgoals(request))
    op = state.before_tool("write_file")
    state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(tmp_path / "out" / "report.csv"))
    state.mark_subgoals(["Write unit tests for the totals"])
    answer = engine._budget_answer("Turn budget exceeded: 10800 seconds elapsed.", state)
    assert "Progress when the run was stopped:" in answer
    assert "- done: Save the report to out/report.csv (file present: report.csv)" in answer
    assert "- not done: Write unit tests for the totals" in answer
    assert "Files changed: report.csv" in answer


def test_a_held_reply_still_comes_first(engine, tmp_path):
    state = TrajectoryState({}, goal="fix it", message_count=1, workspace=str(tmp_path))
    op = state.before_tool("write_file")
    state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(tmp_path / "a.py"))
    state.hold_reply("I fixed a.py and ran it.")
    answer = engine._budget_answer("Turn budget exceeded.", state)
    assert answer.lstrip().startswith(("I fixed a.py", "NOT VERIFIED"))
