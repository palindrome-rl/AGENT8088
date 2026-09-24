"""Only a search that returned results counts as having answered anything.

Live, 2026-09-24: a SearXNG→DDGS approval prompt and an argument-less retry
were the only web_search outcomes, yet the model's page fetch was refused
with "Follow-up fetch was not run — the search already answered this."
_search_was_usable took any non-error text as an answer: the escalation
payload, "Every configured web search provider failed", "No results from
ddgs." That also let the duplicate-search guard serve a failure back as "this
search already ran, answer from these results".
"""
from types import SimpleNamespace as NS

import pytest

from agent8088 import engine as A

REAL = ("Search results (via ddgs):\n\n1. La Liga table\n   https://example.com/\n"
        "   Barcelona lead on 21 points.")


def _framed(text):
    return A._frame_search_results(text)


@pytest.mark.parametrize("result", [
    REAL, _framed(REAL),
    "<<<EXTERNAL_UNTRUSTED_CONTENT source=\"web_search:searxng\">>>\n"
    + REAL.replace("ddgs", "searxng") + "\n<<<END_UNTRUSTED_CONTENT>>>",
])
def test_real_results_are_usable(result):
    assert A._search_was_usable(result)


@pytest.mark.parametrize("result", [
    "",
    "Error: web_search requires 'query'.",
    A.request_escalation(target_mode="edit", paths=["web_search (DDGS): q"],
                         change_type="network_fallback",
                         reason="Local SearXNG returned no results."),
    _framed("Every configured web search provider failed:\n  - ddgs: no results\n"
            "Run `/search doctor` to diagnose."),
    _framed("No results from ddgs."),
    "Permission denied: web_search needs access this run does not have.",
    "No web search provider is configured. Run `/search setup` to provision a local SearXNG",
    "web_search_provider=bogus is not a known provider. Known: searxng, ddgs.",
])
def test_non_results_are_not_usable(result):
    assert not A._search_was_usable(result)


def _response(content):
    return NS(choices=[NS(message=NS(content=content), finish_reason="stop")])


def _run(monkeypatch, search_result):
    replies = iter([
        _response('✿FUNCTION✿: web_search ✿ARGS✿: {"query": "la liga table"}'),
        _response('✿FUNCTION✿: get_page_title ✿ARGS✿: '
                  '{"url": "https://www.laliga.com/en-GB/standings"}'),
        _response("done"),
    ])
    dispatched = []

    def run_tool(name, args, **_kwargs):
        dispatched.append(name)
        return search_result if name == "web_search" else "LaLiga standings"

    monkeypatch.setattr(A, "_native_tools_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(A, "run_tool", run_tool)
    monkeypatch.setattr(A, "_create_completion_with_fallback",
                        lambda *_a, **_k: next(replies))
    A.run_agent([{"role": "user", "content": "la liga current ranking?"}],
                max_turns=5, system_prompt="", memory_capture=False,
                allowed_tools={"web_search", "get_page_title"})
    return dispatched


def test_a_fetch_after_a_failed_search_is_allowed(monkeypatch):
    failed = _framed("Every configured web search provider failed:\n"
                     "  - ddgs: no results\nRun `/search doctor` to diagnose.")
    assert _run(monkeypatch, failed) == ["web_search", "get_page_title"]


def test_a_fetch_after_a_real_search_is_still_refused(monkeypatch):
    assert _run(monkeypatch, _framed(REAL)) == ["web_search"]


def test_a_failed_search_is_not_served_back_as_already_ran(monkeypatch):
    replies = iter([
        _response('✿FUNCTION✿: web_search ✿ARGS✿: {"query": "la liga table"}'),
        _response('✿FUNCTION✿: web_search ✿ARGS✿: {"query": "table la liga"}'),
        _response("done"),
    ])
    outcomes = iter([_framed("No results from ddgs."), _framed(REAL)])
    dispatched = []
    monkeypatch.setattr(A, "_native_tools_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(A, "run_tool",
                        lambda name, args, **_k: dispatched.append(args) or next(outcomes))
    monkeypatch.setattr(A, "_create_completion_with_fallback",
                        lambda *_a, **_k: next(replies))
    A.run_agent([{"role": "user", "content": "la liga current ranking?"}],
                max_turns=5, system_prompt="", memory_capture=False,
                allowed_tools={"web_search"})
    assert len(dispatched) == 2
