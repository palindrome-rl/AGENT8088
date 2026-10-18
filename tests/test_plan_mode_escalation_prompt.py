"""A blocked tool call in plan mode (a web search that wants to leave the local SearXNG) is asked about in the
usual words and can be allowed for the session; only a step of an executing plan gets the plan wording.
Before, every such request said "Approve plan?", offered no session choice, and so asked again for each search."""
from types import SimpleNamespace as N

import pytest

from agent8088 import cli, engine

from tests.test_web_search_fallback_visibility import (  # noqa: F401
    Fake, SearchFailure, SearchSuccess, _local_searxng, _registry, _results, _search, eng)


def _request(change_type="network_fallback"):
    return f"ESCALATION_REQUEST\x1fedit\x1f{change_type}\x1fweb_search (DDGS): pokemon usage\x1freason"


@pytest.fixture
def prompts(monkeypatch):
    seen = N(asked=[], granted=[], printed=[], answers=["once"])

    def choose(title, options, typed_prompt, typed_map, default="deny"):
        seen.asked.append((title, [value for value, _ in options]))
        return seen.answers.pop(0) if seen.answers else default

    monkeypatch.setattr(cli, "_permission_choice", choose)
    monkeypatch.setattr(cli.A, "grant_escalation", lambda change_type="", target="": seen.granted.append(change_type))
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: seen.printed.append(" ".join(str(x) for x in a)))
    monkeypatch.setattr(cli, "_session_allowlist", set())
    return seen


def test_a_search_blocked_in_plan_mode_is_not_called_a_plan(monkeypatch, prompts):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    assert cli._handle_escalation(_request()) is True
    assert prompts.asked == [("Allow this action?", ["once", "session", "deny"])]
    assert not any("Plan approved" in line for line in prompts.printed)


def test_session_is_remembered_so_later_searches_do_not_ask_again(monkeypatch, prompts):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    prompts.answers = ["session"]
    assert cli._handle_escalation(_request()) is True
    assert cli._handle_escalation(_request()) is True
    assert len(prompts.asked) == 1
    assert prompts.granted == ["network_fallback", "network_fallback"]


def test_only_read_only_network_requests_can_be_remembered_in_plan_mode(monkeypatch, prompts):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    assert cli._handle_escalation(_request("edit")) is True
    assert prompts.asked == [("Allow this action?", ["once", "deny"])]


def test_a_step_of_an_executing_plan_keeps_the_plan_wording(monkeypatch, prompts):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    prompts.answers = ["approve"]
    assert cli._handle_escalation(_request(), plan_step=True) is True
    assert prompts.asked == [("Approve plan?", ["approve", "deny"])]


def test_other_modes_are_unchanged(monkeypatch, prompts):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    assert cli._handle_escalation(_request("edit")) is True
    assert prompts.asked == [("Allow this action?", ["once", "session", "deny"])]


def test_a_denial_in_plan_mode_says_plan_mode(monkeypatch, prompts):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    prompts.answers = ["deny"]
    assert cli._handle_escalation(_request()) is False
    assert any("plan-only" in line for line in prompts.printed)
    assert not any("readonly" in line for line in prompts.printed)


def test_the_cause_names_a_searxng_that_is_not_running():
    down = N(failures=("searxng: Could not reach SearXNG at 127.0.0.1:8888",))
    empty = N(failures=("searxng: SearXNG returned no results",))
    assert engine._searxng_fallback_cause(down) == "Local SearXNG is not answering."
    assert engine._searxng_fallback_cause(empty) == "Local SearXNG returned no results."
    assert engine._searxng_fallback_cause(N(failures=())) == "Local SearXNG returned no results."


def test_the_prompt_says_so_when_the_pinned_searxng_is_down(eng, monkeypatch):
    searxng = Fake("searxng", SearchFailure("Could not reach SearXNG at 127.0.0.1:8888"), images=True)
    ddgs = Fake("ddgs", SearchSuccess(_results(5), "ddgs"))
    _registry(eng, monkeypatch, searxng, ddgs)
    _local_searxng(eng)
    eng.set_search_provider("searxng")
    result = _search(eng)
    reason = result.split("\x1f")[4]
    assert reason.startswith("Local SearXNG is not answering.")
    assert "Session" in reason
