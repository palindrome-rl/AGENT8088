"""A web search in plan mode asks the user instead of being refused as a mutation.

Before, a search that could not run without asking (the local SearXNG not answering, or not pinned) got the
"nothing is written or run until the user approves a plan" refusal, which counted as an invalid mutation attempt:
two searches ended the plan session ("Plan mode stopped after 2 invalid mutation attempts"). And a search the
user did approve was refused again, because plan mode ignored the grant."""
import json

import pytest

from tests.test_web_search_fallback_visibility import (  # noqa: F401
    Fake, SearchSuccess, _registry, _results, eng)


@pytest.fixture
def planning(eng, monkeypatch):
    ddgs = Fake("ddgs", SearchSuccess(_results(5), "ddgs"))
    _registry(eng, monkeypatch, ddgs)
    monkeypatch.setattr(eng, "_local_searxng_no_prompt_enabled", lambda: False)
    monkeypatch.setattr(eng, "_ddgs_only_chain", lambda: False)
    eng.set_search_provider("ddgs")
    eng.set_permission_mode("plan-only")
    return ddgs


def _search(eng, query="pokemon usage"):
    return eng.exec_tool("web_search", json.dumps({"query": query}))


def test_a_search_that_must_ask_reaches_the_user_in_plan_mode(eng, planning):
    result = _search(eng)
    assert result.startswith("ESCALATION_REQUEST\x1f")
    assert result.split("\x1f")[2] == "network_request"
    assert "nothing is written or run" not in result
    assert planning.calls == 0


def test_an_approved_search_runs_in_plan_mode(eng, planning):
    first = _search(eng)
    eng._remember_escalation("web_search", {"query": "pokemon usage"}, first)
    eng.grant_escalation("network_request", "web_search: pokemon usage")
    result = _search(eng)
    assert not result.startswith("ESCALATION_REQUEST")
    assert "Search results" in result
    assert planning.calls == 1


def test_the_grant_covers_one_search_and_a_reworded_one_asks_again(eng, planning):
    first = _search(eng)
    eng._remember_escalation("web_search", {"query": "pokemon usage"}, first)
    eng.grant_escalation("network_request", "web_search: pokemon usage")
    assert _search(eng, "something else").startswith("ESCALATION_REQUEST\x1f")
    assert _search(eng).startswith("ESCALATION_REQUEST") is False
    assert _search(eng).startswith("ESCALATION_REQUEST\x1f")


def test_plan_mode_still_refuses_writes_and_the_grant_does_not_unlock_them(eng, planning):
    eng._one_shot_grants.add(eng._ANY_GRANT_KEY)
    result = eng.exec_tool("write_file", json.dumps({"filename": "notes.txt", "content": "x"}))
    assert result.startswith("Error: plan mode")
