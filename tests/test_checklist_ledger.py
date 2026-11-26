"""The acceptance checklist and the ledger.

The parts of a multi-part request become checklist items when the request starts, not only at the final check, so the
model sees them from its first turn. Each item records what kind of thing it asks for, the evidence that settled it,
and the state of the work (the change count) when it was settled. An item that asks for something to run, be tested
or be played with goes back to "recheck" when the work changes after it was settled: evidence from before a change says
nothing about the changed work. Only a passing check clears it again.

The ledger is the task-state note the model reads: the goal, each item with its status and evidence, and the next
step. It is rebuilt from the controller's state on every delivery, so it survives a compaction that drops the
conversation it was in.
"""
import pytest

from agent8088 import completion as c
from agent8088.trajectory import TrajectoryState

REQUEST = """Build a small expense tracker:
1. Save the expenses to data/expenses.json
2. Write unit tests for the totals
3. Run the app and check it starts
4. Clicking Add puts a new row in the table
5. Keep the layout tidy"""


@pytest.mark.parametrize("item, kind", [
    ("Save the expenses to data/expenses.json", "file"),
    ("Write unit tests for the totals", "test"),
    ("Run the app and check it starts", "run"),
    ("Clicking Add puts a new row in the table", "interactive"),
    ("The arrow keys move the paddle", "interactive"),
    ("Keep the layout tidy", "property"),
    ("Install the dependencies with npm", "run"),
])
def test_each_part_gets_a_kind(item, kind):
    assert c.item_kind(item) == kind


def _state(tmp_path, request=REQUEST):
    state = TrajectoryState({}, goal=request, message_count=1, workspace=str(tmp_path))
    state.set_subgoals(c.extract_subgoals(request))
    return state


def _write(state, path):
    op = state.before_tool("write_file")
    state.after_tool(op, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path=str(path))


def _passing_test(state):
    op = state.before_tool("run_tests")
    state.after_tool(op, "5 passed", failed=False, blocked=False, mutated=False, verdict="passed",
                     command="pytest -q")


def test_items_carry_their_kind(tmp_path):
    state = _state(tmp_path)
    kinds = [item["kind"] for item in state.subgoals()]
    assert kinds == ["file", "test", "run", "interactive", "property"]


def test_settling_an_item_records_its_evidence_and_the_state_of_the_work(tmp_path):
    state = _state(tmp_path)
    _write(state, tmp_path / "data" / "expenses.json")
    state.mark_subgoals(["Keep the layout tidy"])
    done = {item["name"]: item for item in state.subgoals() if item["status"] == "done"}
    item = done["Part: Save the expenses to data/expenses.json"]
    assert item["done_at"] == state.mutation_count()
    assert "expenses.json" in item["evidence"]


def test_a_change_after_a_run_item_was_settled_sends_it_back_to_recheck(tmp_path):
    state = _state(tmp_path)
    _passing_test(state)
    state.mark_subgoals([])
    _write(state, tmp_path / "app.py")
    status = {item["name"][len(c.SUBGOAL_PREFIX):]: item["status"] for item in state.subgoals()}
    assert status["Run the app and check it starts"] == "recheck"
    assert status["Write unit tests for the totals"] == "recheck"
    assert status["Clicking Add puts a new row in the table"] == "recheck"
    # What a file or a description asks for is still there after another file changes.
    assert status["Save the expenses to data/expenses.json"] == "done"
    assert status["Keep the layout tidy"] == "done"
    assert state.recheck_subgoals() == ["Write unit tests for the totals", "Run the app and check it starts",
                                        "Clicking Add puts a new row in the table"]


def test_only_a_passing_check_clears_a_recheck(tmp_path):
    state = _state(tmp_path)
    _passing_test(state)
    state.mark_subgoals([])
    _write(state, tmp_path / "app.py")
    state.mark_subgoals([])   # the end-of-run word check alone does not settle stale evidence
    assert state.recheck_subgoals()
    _passing_test(state)
    assert state.recheck_subgoals() == []
    item = next(i for i in state.subgoals() if i["name"].endswith("check it starts"))
    assert item["status"] == "done" and item["done_at"] == state.mutation_count()
    assert "passing check" in item["evidence"]


def test_a_recheck_is_not_an_open_part(tmp_path):
    """Stale evidence is shown, but it does not by itself reopen a part the reply covered."""
    state = _state(tmp_path)
    _passing_test(state)
    state.mark_subgoals([])
    _write(state, tmp_path / "app.py")
    assert state.open_subgoals() == []


def test_the_ledger_lists_items_with_status_evidence_and_the_next_step(tmp_path):
    state = _state(tmp_path)
    _write(state, tmp_path / "data" / "expenses.json")
    state.mark_subgoals(["Write unit tests for the totals", "Run the app and check it starts",
                        "Clicking Add puts a new row in the table", "Keep the layout tidy"])
    note = state.state_note(full=True)
    assert "Goal: Build a small expense tracker" in note
    assert "- [done] Save the expenses to data/expenses.json (file present: expenses.json)" in note
    assert "- [open] Write unit tests for the totals" in note
    assert "Next: Write unit tests for the totals" in note


def test_the_ledger_survives_a_compaction(tmp_path):
    """It is rebuilt from the controller's state, not from the conversation, so dropping messages loses nothing."""
    data = {}
    state = TrajectoryState(data, goal=REQUEST, message_count=1, workspace=str(tmp_path))
    state.set_subgoals(c.extract_subgoals(REQUEST))
    _write(state, tmp_path / "data" / "expenses.json")
    state.mark_subgoals(["Run the app and check it starts"])
    again = TrajectoryState(data, goal=REQUEST, message_count=1, workspace=str(tmp_path))
    note = again.state_note(full=True)
    assert "[done] Save the expenses" in note and "[open] Run the app" in note


def test_the_ledger_stays_short(tmp_path):
    request = "\n".join(f"{n}. Add feature number {n} with its own settings page" for n in range(1, 13))
    state = _state(tmp_path, request)
    _write(state, tmp_path / "a.py")
    note = state.state_note(full=True)
    assert note.count("\n- [") <= c.MAX_SUBGOALS and len(note) < 3000


def test_the_parts_are_known_from_the_start_of_the_request(engine, monkeypatch):
    from tests.test_completion_pipeline import Script
    Script(engine, monkeypatch, ["Done."])
    data = {}
    engine.run_agent([{"role": "user", "content": REQUEST}], max_turns=3, tools_def=[], allowed_tools=set(),
                     trajectory_state=data)
    assert [i["kind"] for i in TrajectoryState(data).subgoals()][:2] == ["file", "test"]
