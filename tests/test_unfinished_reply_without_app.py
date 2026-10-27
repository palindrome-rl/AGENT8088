"""A reply that says the work is unfinished is sent back when the request was a web app and no page or server entry exists yet.

Seen in a demo run: after about 170 turns (of 400 allowed) the model replied "In progress ... I'll continue from the brace fix next",
with a roster, sprites, data and an engine written but no page. The reply admitted the work was unfinished, but the run had no
failing check, no cut-off file and no open part of the request, and it had made changes, so the harness let it end (labelled NOT
VERIFIED: no runnable app, but not sent back to keep working)."""
from agent8088.trajectory import TrajectoryState

REQUEST = "Create a Pokemon Doubles battle simulator web app in which a player battles an AI. Test it by playing one full battle."
UNFINISHED = ("I'm building the Pokemon Doubles simulator. Here's where things stand:\n\n**Done:** roster, sprites, data.js, engine.js.\n\n"
              "**In progress:** I was fixing a brace-balance syntax error in engine.js. Once that's fixed, the remaining work is index.html, "
              "app.js and style.css and a full end-to-end browser test.\n\nI'll continue from the brace fix next.")
FINISHED = "The simulator is built: open index.html, build two teams and play a battle. I ran a full battle with no console errors."
WS = "C:\\w\\proj"


def _state(request=REQUEST, written=("data.js", "engine.js", "roster.json")):
    state = TrajectoryState({}, goal=request, message_count=1, workspace=WS)
    for name in written:
        operation = state.before_tool("write_file")
        state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path=f"{WS}\\{name}")
    state.data["verification"] = "inspected"      # read back, nothing failing, nothing open: the state the real run ended in
    return state


def test_the_reported_case_is_sent_back(engine):
    state = _state()
    assert not state.has_open_items()
    assert state.no_runnable_app()
    assert engine._reply_admits_unfinished(state, UNFINISHED, REQUEST)


def test_a_page_written_means_the_old_rules_apply(engine):
    state = _state(written=("data.js", "engine.js", "index.html"))
    assert not state.no_runnable_app()
    assert not engine._reply_admits_unfinished(state, UNFINISHED, REQUEST)


def test_a_server_entry_counts_as_an_app(engine):
    state = _state(written=("data.js", "server.py"))
    assert not engine._reply_admits_unfinished(state, UNFINISHED, REQUEST)


def test_a_reply_that_does_not_admit_it_is_left_alone(engine):
    assert not engine._reply_admits_unfinished(_state(), FINISHED, REQUEST)


def test_a_request_that_is_not_a_web_app_is_unaffected(engine):
    request = "Write a Python script that renames the photos in a folder by date."
    state = _state(request=request, written=("rename.py",))
    assert not state.no_runnable_app()
    assert not engine._reply_admits_unfinished(state, UNFINISHED, request)


def test_a_question_about_a_web_app_is_unaffected(engine):
    request = "How would you structure a web app for a battle simulator? Just explain, don't build anything."
    state = TrajectoryState({}, goal=request, message_count=1, workspace=WS)
    assert not engine._reply_admits_unfinished(state, UNFINISHED, request)


def test_open_items_still_count_as_before(engine):
    state = _state(written=("data.js", "index.html"))
    state.data["verification"] = "pending"
    assert state.has_open_items()
    assert engine._reply_admits_unfinished(state, UNFINISHED, REQUEST)


def test_missing_app_continuation_uses_existing_budget_and_records_exhaustion(engine):
    state = _state()
    budget = engine.completion_checks.ContinuationBudget(limit=2)
    for _ in range(2):
        nudge = engine._next_completion_nudge(
            budget, state, reply=UNFINISHED, request=REQUEST, depth=0)
        assert nudge.kind == "unfinished"
        assert "Continue the task now" in nudge.text
    assert engine._next_completion_nudge(
        budget, state, reply=UNFINISHED, request=REQUEST, depth=0) is None
    assert budget.total == 2
    assert "the reply says the work is unfinished" in state.data["unmet_checks"]


def test_disabled_completion_checks_do_not_continue_missing_app(engine):
    budget = engine.completion_checks.ContinuationBudget(limit=0)
    assert engine._next_completion_nudge(
        budget, _state(), reply=UNFINISHED, request=REQUEST, depth=0) is None
    assert budget.total == 0


def test_report_only_records_unfinished_missing_app_without_spending_budget(engine, monkeypatch):
    state = _state()
    budget = engine.completion_checks.ContinuationBudget(limit=2)
    monkeypatch.setattr(engine, "COMPLETION_SMOKE_TEST", "off")
    monkeypatch.setattr(engine, "_missing_required_files", lambda *_a: [])
    reasons = engine._record_unmet_checks(
        budget, state, reply=UNFINISHED, request=REQUEST, depth=0)
    assert "the reply says the work is unfinished" in reasons
    assert budget.total == 0
