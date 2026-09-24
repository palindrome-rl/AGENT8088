"""One request gets a bounded number of web searches, then must answer.

Observed live with glm-5.3 on the DDGS backend: asked for "la liga current
ranking", the model chased a complete 20-team table that five-result snippets
never contain, rephrasing the query up to 23 times until a five-minute limit
killed the turn with no answer at all. Nothing but max_turns bounded it.
"""
from types import SimpleNamespace as NS

from agent8088 import engine as A

RESULT = "Search results (via ddgs):\n\n1. La Liga table\n   https://example.com/"


def _response(content):
    return NS(choices=[NS(message=NS(content=content), finish_reason="stop")])


def _search(query):
    return _response(f'✿FUNCTION✿: web_search ✿ARGS✿: {{"query": "{query}"}}')


def _run(monkeypatch, replies, run_tool=None, on_escalation=None, cap=None):
    responses = iter(replies)
    dispatched, prompts = [], []

    def default_tool(name, args, **_kwargs):
        dispatched.append(dict(args))
        return RESULT

    def completion(messages, *_a, **_k):
        prompts.extend(str(m.get("content") or "") for m in messages)
        return next(responses)

    if cap is not None:
        monkeypatch.setitem(A.APP_CONFIG, "web_search_max_per_turn", str(cap))
    monkeypatch.setattr(A, "_native_tools_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(A, "run_tool", run_tool or default_tool)
    monkeypatch.setattr(A, "_create_completion_with_fallback", completion)
    answer = A.run_agent(
        [{"role": "user", "content": "la liga current ranking?"}], max_turns=20,
        system_prompt="", allowed_tools={"web_search"}, memory_capture=False,
        on_escalation=on_escalation,
    )
    return answer, dispatched, prompts


def test_searches_stop_at_the_cap_and_the_model_is_told_to_answer(monkeypatch):
    replies = [_search(f"la liga table variant {i}") for i in range(8)] + [
        _response("Barcelona lead on 21 points.")]

    answer, dispatched, prompts = _run(monkeypatch, replies, cap=6)

    assert len(dispatched) == 6
    assert any("Search limit reached" in p for p in prompts)
    assert "Barcelona" in answer


def test_default_cap_is_six(monkeypatch):
    monkeypatch.delitem(A.APP_CONFIG, "web_search_max_per_turn", raising=False)
    replies = [_search(f"la liga table variant {i}") for i in range(9)] + [
        _response("done")]

    _answer, dispatched, _prompts = _run(monkeypatch, replies)

    assert len(dispatched) == 6


def test_zero_disables_the_cap(monkeypatch):
    replies = [_search(f"la liga table variant {i}") for i in range(9)] + [
        _response("done")]

    _answer, dispatched, _prompts = _run(monkeypatch, replies, cap=0)

    assert len(dispatched) == 9


def test_a_search_under_the_cap_is_unaffected(monkeypatch):
    replies = [_search("la liga table"), _response("done")]

    answer, dispatched, prompts = _run(monkeypatch, replies, cap=6)

    assert dispatched == [{"query": "la liga table"}]
    assert not any("Search limit reached" in p for p in prompts)
    assert "done" in answer


def test_escalated_and_malformed_calls_do_not_use_up_the_cap(monkeypatch):
    """Only searches that actually ran count: an approval prompt and a call
    that arrived without its query spent no search."""
    calls = []

    def run_tool(name, args, **_kwargs):
        calls.append(dict(args))
        if not args.get("query"):
            return "Error: web_search requires 'query'."
        if len(calls) == 1:
            return A.request_escalation(
                target_mode="edit", paths=["web_search (DDGS): q"],
                change_type="network_fallback", reason="SearXNG returned nothing.")
        return RESULT

    replies = [
        _search("la liga table variant 0"),                 # escalates
        _search("la liga table variant 0"),                 # approved retry
        _response('✿FUNCTION✿: web_search ✿ARGS✿: {}'),     # malformed
        _search("la liga table variant 1"),
        _response("done"),
    ]

    _answer, _dispatched, prompts = _run(
        monkeypatch, replies, run_tool=run_tool, on_escalation=lambda *_a: True, cap=2)

    ran = [c for c in calls if c.get("query")]
    assert ran == [{"query": "la liga table variant 0"},
                   {"query": "la liga table variant 0"},
                   {"query": "la liga table variant 1"}]
    assert not any("Search limit reached" in p for p in prompts)


def test_a_cached_repeat_does_not_use_up_the_cap(monkeypatch):
    replies = [_search("la liga table"), _search("table la liga"),
               _search("la liga standings"), _response("done")]

    _answer, dispatched, prompts = _run(monkeypatch, replies, cap=2)

    assert dispatched == [{"query": "la liga table"}, {"query": "la liga standings"}]
    assert not any("Search limit reached" in p for p in prompts)
