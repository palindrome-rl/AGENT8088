"""A garbled ✿ARGS✿ delimiter must not cost a tool call its arguments.

Observed live from glm-5.3 via Ollama Cloud: the model sometimes corrupts the
closing flower of the ARGS marker — "✿ARGS✟ {…}" (no colon) or
"✿ARGS�<arg_value>: {…}</arg_value></tool_call>". The parser still
found the function name but not the argument object, so the call dispatched
as web_search({}) and the tool answered "requires 'query'". When that call
was the retry of a search the user had just approved to fall back to DDGS,
the approved search never ran at all.
"""
import json
from types import SimpleNamespace as NS

import pytest

from agent8088 import engine as A

QUERY = "La Liga standings table current 2026-27 season"

LIVE_GARBLED = [
    # Both taken verbatim from glm-5.3 session traces.
    f'✿FUNCTION✿: web_search ✿ARGS✟ {{"query": "{QUERY}"}}',
    f'✿FUNCTION✿: web_search ✿ARGS�<arg_value>: {{"query": "{QUERY}"}}'
    "</arg_value></tool_call>",
]

OTHER_GARBLED = [
    f'✿FUNCTION✿: web_search ✿ARGS {{"query": "{QUERY}"}}',
    f'✿FUNCTION✿: web_search ✿ARGS✟: {{"query": "{QUERY}"}}',
    f'✿FUNCTION✿: web_search ✿ARGS�� {{"query": "{QUERY}"}}',
    f'✿FUNCTION✿: web_search ✿ARGS✿ {{"query": "{QUERY}"}}',
    f'✿FUNCTION✿: web_search ✿ARGS✿:{{"query": "{QUERY}"}}',
]


@pytest.mark.parametrize("text", LIVE_GARBLED + OTHER_GARBLED)
def test_garbled_args_marker_keeps_the_arguments(text):
    assert A.find_tool_calls(text, {"web_search"}) == [
        {"name": "web_search", "arguments": {"query": QUERY}}]


def test_canonical_marker_is_unchanged():
    text = f'✿FUNCTION✿: web_search ✿ARGS✿: {{"query": "{QUERY}"}}'
    assert A._normalize_tool_markers(text) == text


def test_garbled_marker_keeps_nested_arguments():
    args = {"name": "gimp", "arguments": ["--json", "export"], "opts": {"deep": True}}
    text = f"✿FUNCTION✿: cli_anything_run ✿ARGS✟ {json.dumps(args)}"
    assert A.find_tool_calls(text, {"cli_anything_run"}) == [
        {"name": "cli_anything_run", "arguments": args}]


def test_every_garbled_call_in_a_batch_keeps_its_arguments():
    text = ('✿FUNCTION✿: web_search ✿ARGS✟ {"query": "first"}\n'
            '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "second"}\n'
            '✿FUNCTION✿: web_search ✿ARGS�<arg_value>: {"query": "third"}</arg_value>')
    assert [c["arguments"] for c in A.find_tool_calls(text, {"web_search"})] == [
        {"query": "first"}, {"query": "second"}, {"query": "third"}]


def test_garbled_marker_does_not_adopt_json_from_another_line():
    """Only the same line counts: a later, unrelated object is not this call's."""
    text = ('✿FUNCTION✿: web_search ✿ARGS✟ see below\n'
            'Example payload: {"query": "not mine"}')
    calls = A.find_tool_calls(text, {"web_search"})
    assert calls and calls[0]["arguments"].get("query") != "not mine"


def test_garbled_marker_does_not_swallow_the_next_call():
    """The junk between ARGS and '{' must not span into another call's header."""
    text = ('✿FUNCTION✿: web_search ✿ARGS✿: "x" ✿FUNCTION✿: web_search '
            '✿ARGS✿: {"query": "second"}')
    calls = A.find_tool_calls(text, {"web_search"})
    assert {"query": "second"} in [c["arguments"] for c in calls]


def test_garbled_marker_inside_a_fence_still_does_not_execute():
    text = "Example:\n```\n" + LIVE_GARBLED[0] + "\n```\nThat is the format."
    assert A.find_tool_calls(text, {"web_search"}) == []


@pytest.mark.parametrize("text", LIVE_GARBLED)
def test_garbled_call_is_stripped_from_the_answer(text):
    cleaned = A.strip_tool_json(f"Here you go.\n{text}")
    assert cleaned == "Here you go."


@pytest.mark.parametrize("text", LIVE_GARBLED)
def test_garbled_call_is_scrubbed_from_web_display(text):
    from agent8088.web_server import scrub_markup
    cleaned = scrub_markup(f"Here you go. {text}")
    assert cleaned.strip() == "Here you go."
    assert QUERY not in cleaned


def _response(content):
    return NS(choices=[NS(message=NS(content=content), finish_reason="stop")])


def test_approved_fallback_retry_with_garbled_marker_runs_the_search(monkeypatch):
    """The live sequence: search escalates, user approves, the model's retry
    comes back with a garbled marker. The retry must reach the tool with the
    same query instead of as web_search({})."""
    responses = iter([
        _response(f'✿FUNCTION✿: web_search ✿ARGS✿: {{"query": "{QUERY}"}}'),
        _response(LIVE_GARBLED[0]),
        _response("done"),
    ])
    dispatched = []

    def run_tool(name, args, **_kwargs):
        dispatched.append((name, dict(args)))
        if len(dispatched) == 1:
            return A.request_escalation(
                target_mode="edit", paths=[f"web_search (DDGS): {QUERY}"],
                change_type="network_fallback",
                reason="Local SearXNG returned no results.")
        return "Search results (via ddgs):\n\n1. La Liga table\n   https://example.com/"

    monkeypatch.setattr(A, "_native_tools_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(A, "run_tool", run_tool)
    monkeypatch.setattr(A, "_create_completion_with_fallback",
                        lambda *_a, **_k: next(responses))

    answer = A.run_agent(
        [{"role": "user", "content": "la liga current ranking?"}], max_turns=5,
        system_prompt="", allowed_tools={"web_search"}, memory_capture=False,
        on_escalation=lambda *_a: True,
    )

    assert dispatched == [("web_search", {"query": QUERY}),
                          ("web_search", {"query": QUERY})]
    assert "done" in answer
