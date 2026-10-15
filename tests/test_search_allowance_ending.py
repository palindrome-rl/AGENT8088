"""A research request that keeps finding new pages gets further, and one that is refused ends with files, not with nothing."""
from types import SimpleNamespace as NS

import pytest

from agent8088 import engine as A


def _response(content):
    return NS(choices=[NS(message=NS(content=content), finish_reason="stop")])


def _search(query):
    return f'✿FUNCTION✿: web_search ✿ARGS✿: {{"query": "{query}"}}'


def _fresh(query):
    slug = query.replace(" ", "-")
    lines = ["Search results (via ddgs):", ""]
    for n in range(5):
        lines += [f"{n + 1}. Page {n}", f"   https://example.com/{slug}/{n}"]
    return "\n".join(lines)


# --- the allowance -------------------------------------------------------------------------------

def test_two_productive_searches_in_a_row_still_earn_more_up_to_three_times_the_cap():
    assert A._grown_search_allowance(6, 6, [True, True]) == 9
    assert A._grown_search_allowance(15, 6, [True] * 15) == 18


def test_past_three_times_the_cap_it_takes_three_in_a_row():
    assert A._grown_search_allowance(18, 6, [True] * 17 + [False, True]) == 18
    assert A._grown_search_allowance(18, 6, [True] * 18) == 21


def test_growth_never_passes_the_ceiling_and_unproductive_searches_earn_nothing(monkeypatch):
    assert A._grown_search_allowance(36, 6, [True] * 40) == 36
    assert A._grown_search_allowance(6, 6, [True, False, True, False]) == 6


# --- what the model is told and offered once the allowance is spent ----------------------------------------

def _scripted(monkeypatch, replies, *, cap=3, request="research this"):
    seen, tools_offered = [], []
    queue = iter(replies)

    def completion(messages, tools=None, **_k):
        seen.append(str(messages[-1].get("content") or ""))
        tools_offered.append(sorted(t["function"]["name"] for t in (tools or []) if isinstance(t, dict) and "function" in t))
        return _response(next(queue, "Final answer."))

    monkeypatch.setitem(A.APP_CONFIG, "web_search_max_per_turn", str(cap))
    monkeypatch.setitem(A.APP_CONFIG, "web_search_ceiling_multiplier", "1")
    monkeypatch.setattr(A, "_native_tools_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(A, "run_tool", lambda name, args, **_k: _fresh(args["query"]))
    monkeypatch.setattr(A, "_create_completion_with_fallback", completion)
    answer = A.run_agent([{"role": "user", "content": request}], max_turns=40, system_prompt="",
                         allowed_tools={"web_search"}, memory_capture=False)
    return answer, seen


def test_the_refusal_names_what_to_do_next_and_is_not_a_strike(monkeypatch):
    # three searches use the allowance; a batch of two is refused; the next call is to a tool that is no longer there
    replies = [_search("a one") + "\n" + _search("a two") + "\n" + _search("a three"),
               _search("b one") + "\n" + _search("b two"),
               _search("c one"),
               "Here is what I found."]
    answer, seen = _scripted(monkeypatch, replies)
    refusals = [m for m in seen if "Search limit reached" in m]
    assert refusals and "fetch it with the shell or a read tool" in refusals[0] and "go on to the work now" in refusals[0]
    assert not any("You keep repeating tool calls without progress" in m for m in seen)
    assert any("web_search is not available any more" in m for m in seen)
    assert answer.startswith("Here is what I found.")


def test_a_build_request_with_nothing_written_gets_one_extra_chance_before_it_can_end(monkeypatch):
    # the model keeps calling the refused tool; after the first refusal each further round is a correction
    replies = [_search(f"topic {i}") for i in range(20)] + ["Not able to continue."]
    answer, seen = _scripted(monkeypatch, replies, cap=2, request="Build a web page about animals")
    chances = [m for m in seen if "Start writing the files now" in m]
    assert len(chances) == 1


def test_a_question_that_is_not_a_build_request_ends_as_before(monkeypatch):
    replies = [_search(f"topic {i}") for i in range(20)] + ["Not able to continue."]
    _answer, seen = _scripted(monkeypatch, replies, cap=2, request="How many pandas are left?")
    assert not any("Start writing the files now" in m for m in seen)
