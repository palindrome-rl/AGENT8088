"""Interactive work is play-tested, and built so it can be.

A part of the request that asks for an interaction (a click, a key, a drag, a game) is only shown to work by playing
it: a passing browser_test. Loading the page or a unit test of some helper does not show it. So:

- A passing browser_test records the state of the work it played; an interactive part settled at that state says
  "a passing play test", one settled otherwise says it was not play-tested.
- An interactive part waiting for a recheck is settled again only by a passing browser_test.
- The verdict is not VERIFIED while a done interactive part was not play-tested since the last change, and the ledger
  names the play test as the next step. Nothing here blocks a run or reopens a part, so the saved runs' verdicts are
  unchanged.
- The browser-qa skill teaches the test seam: a small read-only state function and an optional seed, so a test can
  drive the game without waiting on real timers and without changing normal play.
"""
from pathlib import Path

import pytest

from agent8088 import completion as c
from agent8088.trajectory import TrajectoryState

REQUEST = "1. Draw the board in game.html\n2. Clicking a tile flips it\n3. Write unit tests for the scoring"


@pytest.fixture
def state(tmp_path):
    s = TrajectoryState({}, goal=REQUEST, message_count=1, workspace=str(tmp_path))
    s.set_subgoals(c.extract_subgoals(REQUEST))
    return s


def _write(s, path):
    op = s.before_tool("write_file")
    s.after_tool(op, "ok", failed=False, blocked=False, mutated=True, path=str(path))


def _passed(s, tool, command=""):
    op = s.before_tool(tool)
    s.after_tool(op, "passed", failed=False, blocked=False, mutated=False, verdict="passed", command=command)


def _status(s, fragment):
    return next(i for i in s.subgoals() if fragment in i["name"])


def test_a_play_test_is_the_evidence_for_an_interactive_part(state, tmp_path):
    _write(state, tmp_path / "game.html")
    _passed(state, "browser_test")
    state.mark_subgoals([])
    assert _status(state, "Clicking a tile")["evidence"] == "a passing play test"
    assert state.unplayed_interactive() == []


def test_a_unit_test_alone_does_not_play_an_interactive_part(state, tmp_path):
    _write(state, tmp_path / "game.html")
    _passed(state, "run_tests", "pytest -q")
    state.mark_subgoals([])
    assert "not play-tested" in _status(state, "Clicking a tile")["evidence"]
    assert state.unplayed_interactive() == ["Clicking a tile flips it"]


def test_a_change_after_the_play_test_needs_another(state, tmp_path):
    _write(state, tmp_path / "game.html")
    _passed(state, "browser_test")
    state.mark_subgoals([])
    _write(state, tmp_path / "game.js")
    assert _status(state, "Clicking a tile")["status"] == "recheck"
    _passed(state, "run_tests", "pytest -q")       # not a play test
    assert _status(state, "Clicking a tile")["status"] == "recheck"
    assert _status(state, "unit tests")["status"] == "done"
    _passed(state, "browser_test")
    assert _status(state, "Clicking a tile")["status"] == "done"


def test_the_verdict_waits_for_the_play_test(engine, state, tmp_path):
    _write(state, tmp_path / "game.html")
    _passed(state, "run_tests", "pytest -q")
    state.mark_subgoals([])
    level, why = engine._verdict(state, "Done.")
    assert level == "PARTLY VERIFIED" and "not play-tested" in why
    _passed(state, "browser_test")
    state.data["subgoals_built"] = True
    for item in state.subgoals():
        item["status"], item["evidence"] = "pending", ""
    state.mark_subgoals([])
    assert engine._verdict(state, "Done.")[0] == "VERIFIED"


def test_the_ledger_names_the_play_test_as_next(state, tmp_path):
    _write(state, tmp_path / "game.html")
    state.mark_subgoals([])
    note = state.state_note(full=True)
    assert "Next: play-test \"Clicking a tile flips it\" with browser_test" in note


def test_nothing_here_reopens_a_part_or_changes_the_saved_runs_verdict(engine, state, tmp_path):
    _write(state, tmp_path / "game.html")
    state.mark_subgoals([])
    assert state.open_subgoals() == []
    assert engine._not_verified_reasons(state, "Done.") == []


def test_the_skill_teaches_the_test_seam():
    from agent8088 import engine as e
    text = (Path(e.__file__).parent / "skills_installed" / "browser-qa" / "SKILL.md").read_text(encoding="utf-8")
    assert "test seam" in text.lower()
    for idea in ("state()", "seed", "must not change", "expect_js"):
        assert idea in text, idea
