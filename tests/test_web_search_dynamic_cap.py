"""The per-request search cap grows while searches keep finding new results.

A fixed cap of 6 stopped real research mid-way even when every search was
bringing back pages the run had not seen. The cap still holds a model that
rephrases one question (its searches return the same pages), and a hard
ceiling bounds growth so a run can never search forever.
"""
from types import SimpleNamespace as NS

from agent8088 import engine as A


def _response(content):
    return NS(choices=[NS(message=NS(content=content), finish_reason="stop")])


def _search(query):
    return _response(f'✿FUNCTION✿: web_search ✿ARGS✿: {{"query": "{query}"}}')


def _results(*urls):
    lines = ["Search results (via ddgs):", ""]
    for index, url in enumerate(urls, 1):
        lines += [f"{index}. Page {index}", f"   {url}"]
    return "\n".join(lines)


def _fresh(query):
    """Five pages no other query returns: a search that found new material."""
    slug = query.replace(" ", "-")
    return _results(*(f"https://example.com/{slug}/{n}" for n in range(5)))


def _same(_query):
    """The same five pages every time: a rephrasing of one question."""
    return _results(*(f"https://example.com/table/{n}" for n in range(5)))


def _run(monkeypatch, searches, results, *, cap=6, multiplier=None, extension=None):
    replies = iter([_search(f"topic {i}") for i in range(searches)] + [_response("done")])
    dispatched, prompts = [], []

    def run_tool(name, args, **_kwargs):
        dispatched.append(args["query"])
        return results(args["query"])

    def completion(messages, *_a, **_k):
        prompts.extend(str(m.get("content") or "") for m in messages)
        return next(replies)

    monkeypatch.setitem(A.APP_CONFIG, "web_search_max_per_turn", str(cap))
    for key, value in (("web_search_ceiling_multiplier", multiplier),
                       ("web_search_extension", extension)):
        if value is None:
            monkeypatch.delitem(A.APP_CONFIG, key, raising=False)  # test the default
        else:
            monkeypatch.setitem(A.APP_CONFIG, key, str(value))
    monkeypatch.setattr(A, "_native_tools_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(A, "run_tool", run_tool)
    monkeypatch.setattr(A, "_create_completion_with_fallback", completion)
    A.run_agent(
        [{"role": "user", "content": "research this"}], max_turns=40,
        system_prompt="", allowed_tools={"web_search"}, memory_capture=False,
    )
    return dispatched, prompts


def test_searches_finding_new_pages_run_past_the_starting_cap(monkeypatch):
    dispatched, prompts = _run(monkeypatch, 10, _fresh)

    assert len(dispatched) == 10
    assert not any("Search limit reached" in p for p in prompts)


def test_rephrasing_one_question_still_stops_at_the_cap(monkeypatch):
    dispatched, prompts = _run(monkeypatch, 10, _same)

    assert len(dispatched) == 6
    assert any("Search limit reached" in p for p in prompts)


def test_growth_stops_at_the_hard_ceiling(monkeypatch):
    # Default ceiling: 6 x the starting cap.
    dispatched, prompts = _run(monkeypatch, 45, _fresh)

    assert len(dispatched) == 36
    assert any("Search limit reached: 36 web searches" in p for p in prompts)


def test_a_fixed_ceiling_of_three_times_the_cap_is_still_available(monkeypatch):
    dispatched, prompts = _run(monkeypatch, 25, _fresh, multiplier=3)

    assert len(dispatched) == 18
    assert any("Search limit reached: 18 web searches" in p for p in prompts)


def test_growth_stops_once_searches_stop_finding_new_pages(monkeypatch):
    def results(query):
        index = int(query.split()[-1])
        return _fresh(query) if index < 6 else _same(query)

    dispatched, _prompts = _run(monkeypatch, 15, results)

    # 6 fresh searches earn one extension (6 -> 9); the repeats that follow
    # earn nothing, so the run stops at the extended allowance.
    assert len(dispatched) == 9


def test_multiplier_of_one_keeps_the_cap_fixed(monkeypatch):
    dispatched, _prompts = _run(monkeypatch, 10, _fresh, multiplier=1)

    assert len(dispatched) == 6


def test_extension_size_is_configurable(monkeypatch):
    def results(query):
        index = int(query.split()[-1])
        return _fresh(query) if index < 6 else _same(query)

    dispatched, _prompts = _run(monkeypatch, 15, results, extension=5)

    assert len(dispatched) == 11


def test_zero_still_disables_the_cap(monkeypatch):
    dispatched, _prompts = _run(monkeypatch, 25, _same, cap=0)

    assert len(dispatched) == 25


def test_new_page_counting_ignores_trailing_slashes_and_fragments():
    urls = A._search_result_urls(_results("https://Example.com/a/", "https://example.com/a#top",
                                          "https://example.com/b"))
    assert urls == {"https://example.com/a", "https://example.com/b"}


def _empty(_query):
    """What DDGS returns when it throttles a burst of searches."""
    return "No results found (tried: ddgs). Try a broader or differently worded query."


def test_empty_searches_neither_earn_nor_block_more(monkeypatch):
    """Seen live: DDGS answered searches 5 and 6 of ten distinct city lookups
    with "No results found". Counting those as "no new pages" stopped real
    work at 6, the same as a rephrasing loop."""
    def results(query):
        index = int(query.split()[-1])
        return _empty(query) if index in (4, 5) else _fresh(query)

    dispatched, prompts = _run(monkeypatch, 10, results)

    assert len(dispatched) == 10
    assert not any("Search limit reached" in p for p in prompts)


def test_empty_searches_alone_cannot_chain_extensions(monkeypatch):
    def results(query):
        index = int(query.split()[-1])
        return _fresh(query) if index < 6 else _empty(query)

    dispatched, _prompts = _run(monkeypatch, 15, results)

    # 6 fresh searches earn one extension (6 -> 9); the empty ones after it
    # found nothing new, so they cannot earn the next.
    assert len(dispatched) == 9
