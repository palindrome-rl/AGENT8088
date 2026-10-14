"""A build request that must follow real rules or data, finished without any lookup, is
sent back once to check its facts and name its sources."""
import json
from types import SimpleNamespace as N

import pytest

from agent8088 import completion as c
from agent8088.trajectory import TrajectoryState as Trajectory

BUILD = ("Create a Zorblax Cargo Planner web app that lists the full roster of Zorblax freighters. "
         "Each freighter's hold size and engine options should be based on what is valid and "
         "available in Zorblax Logistics, and users should be able to pick a route.")


# --- the signal ----------------------------------------------------------------------------

def test_a_build_request_that_needs_real_data_and_names_its_subject_is_flagged():
    assert c.grounding_subject(BUILD) == "Zorblax Cargo Planner"


@pytest.mark.parametrize("request_text", [
    # nothing in it depends on outside facts
    "Build a small todo web app with add and remove, and make it look clean and modern please.",
    # the facts are supplied
    "Write a scraper for the official Acme Payments API docs that lists all valid endpoints, "
    "based on https://docs.acme.example/api",
    "Create a report generator that is accurate and uses the numbers in the attached spreadsheet "
    "for the Orion Fleet Division this quarter.",
    "Build a tool that uses the actual data from the file sales_2025.csv for the Orion Fleet Division.",
    # not a build request
    "What is the official capital of the Zorblax Republic according to the full list of states?",
    # too short to be a considered request
    "Make it official Zorblax Logistics",
])
def test_requests_that_do_not_depend_on_outside_facts_are_not_flagged(request_text):
    assert c.grounding_subject(request_text) == ""


def test_the_nudge_names_the_subject_and_asks_for_sources():
    nudge = c.grounding_nudge("Zorblax Cargo Planner")
    assert nudge.kind == "grounding"
    assert "Zorblax Cargo Planner" in nudge.text and "web_search" in nudge.text and "sources" in nudge.text


# --- what counts as having looked something up ----------------------------------------------

def _done(t, tool, command="", status="done"):
    op = t.before_tool(tool)
    t.after_tool(op, "ok", failed=status != "done", blocked=False, mutated=False, command=command)


def test_a_search_a_browsed_page_or_a_fetch_from_the_shell_counts():
    for tool, command in (("web_search", ""), ("browse_page", ""), ("execute_shell", "curl -s https://example.org/data.json")):
        t = Trajectory({}, goal="x", message_count=1)
        assert not t.research_used()
        _done(t, tool, command)
        assert t.research_used(), tool


def test_a_failed_search_or_a_local_command_does_not_count():
    t = Trajectory({}, goal="x", message_count=1)
    _done(t, "web_search", status="failed")
    _done(t, "execute_shell", "ls -la")
    _done(t, "read_text")
    assert not t.research_used()


# --- through the agent loop -----------------------------------------------------------------

def _reply(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _write(path, content):
    return "✿FUNCTION✿: write_file ✿ARGS✿: " + json.dumps({"filename": str(path), "content": content})


@pytest.fixture
def eng(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path
    engine.PLAN_AUDIT = False
    engine.reset_turn_counters()
    monkeypatch.setattr(engine, "GROUNDING_CHECK", True)
    monkeypatch.setattr(engine, "COMPLETION_SMOKE_TEST", "off")
    monkeypatch.setattr(engine, "REQUIREMENT_EVIDENCE", False, raising=False)
    return engine


def _run(engine, monkeypatch, script, tools=None, request=BUILD):
    sent = []
    real = engine.exec_tool

    def model(messages, tools_, **kwargs):
        sent.append(str(messages[-1].get("content")))
        return _reply(script(len(sent) - 1))

    def tool(name, arguments, depth=0):
        if name == "web_search":
            return "Search results: Zorblax freighters have 3 hold classes (source: zorblax.example/ships)."
        return real(name, arguments, depth)

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "exec_tool", tool)
    kwargs = {"allowed_tools": tools} if tools else {}
    answer = engine.run_agent([{"role": "user", "content": request}], max_turns=12,
                              dynamic_turns=False, **kwargs)
    return answer, sent


def test_files_written_without_a_lookup_are_sent_back_once(eng, monkeypatch, tmp_path):
    target = tmp_path / "planner.py"

    def script(i):
        return [_write(target, "SHIPS = ['A']\n"),
                "Done: the planner is finished.",
                '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "Zorblax freighter hold classes"}',
                "Checked the hold classes (source: zorblax.example/ships); the planner is finished."][min(i, 3)]

    answer, sent = _run(eng, monkeypatch, script)
    asked = [s for s in sent if "depends on real facts about Zorblax Cargo Planner" in s]
    assert len(asked) == 1
    assert "zorblax.example/ships" in answer


def test_it_is_asked_once_even_if_the_model_does_not_search(eng, monkeypatch, tmp_path):
    target = tmp_path / "planner.py"

    def script(i):
        return _write(target, "SHIPS = ['A']\n") if i == 0 else "Done."

    answer, sent = _run(eng, monkeypatch, script)
    assert sum("depends on real facts" in s for s in sent) == 1


def test_a_run_that_already_searched_is_not_asked(eng, monkeypatch, tmp_path):
    target = tmp_path / "planner.py"

    def script(i):
        return ['✿FUNCTION✿: web_search ✿ARGS✿: {"query": "Zorblax freighters"}',
                _write(target, "SHIPS = ['A']\n"), "Done."][min(i, 2)]

    _, sent = _run(eng, monkeypatch, script)
    assert not any("depends on real facts" in s for s in sent)


def test_nothing_is_asked_without_a_search_tool_or_with_the_check_off(eng, monkeypatch, tmp_path):
    target = tmp_path / "planner.py"

    def script(i):
        return _write(target, "SHIPS = ['A']\n") if i == 0 else "Done."

    _, sent = _run(eng, monkeypatch, script, tools={"write_file", "read_text"})
    assert not any("depends on real facts" in s for s in sent)
    monkeypatch.setattr(eng, "GROUNDING_CHECK", False)
    eng.reset_turn_counters()
    _, sent = _run(eng, monkeypatch, script)
    assert not any("depends on real facts" in s for s in sent)
