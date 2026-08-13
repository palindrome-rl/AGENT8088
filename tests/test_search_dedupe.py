"""Equivalent-query detection — the exact-match guard was never enough.

The loop compares json.dumps(args), so one changed character reads as a brand
new call. "latest python release" and "Latest Python release?" both ran, and a
model that rephrases when it doesn't like an answer can burn a whole turn
budget asking one question.
"""


def _sig(engine, query):
    return engine._search_signature(query)


def test_case_and_punctuation_do_not_make_a_new_query(engine):
    assert _sig(engine, "Latest Python release?") == _sig(engine, "latest python release")


def test_word_order_does_not_make_a_new_query(engine):
    assert _sig(engine, "python latest release") == _sig(engine, "latest python release")


def test_filler_words_do_not_make_a_new_query(engine):
    assert _sig(engine, "what is the latest python release") == \
        _sig(engine, "latest python release")


def test_genuinely_different_queries_differ(engine):
    assert _sig(engine, "latest python release") != _sig(engine, "latest ruby release")


def test_an_empty_query_has_a_signature(engine):
    assert _sig(engine, "") == ()


# --- loop behaviour -------------------------------------------------------

def _search_returning(engine, monkeypatch, results):
    """Wire a search that yields `results` in order, recording each query."""
    runs = []
    queue = list(results)

    def _run(query, *a, **k):
        runs.append(query)
        return queue.pop(0) if queue else "no more"

    monkeypatch.setattr(engine.web_search, "run_search", _run)
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)
    return runs


def _drive(engine, monkeypatch, scripted, responses):
    """Run one turn against a scripted model.

    ScriptedModel takes create_completion's (client, messages, tools); the
    fallback wrapper the loop actually calls takes (messages, tools), so it is
    adapted here rather than reshaping the shared fixture.
    """
    model = scripted(responses)
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda messages, tools, **kw: model(None, messages, tools, **kw))
    engine.run_agent([{"role": "user", "content": "latest python?"}], max_turns=6)
    return model


def test_reworded_search_is_not_re_run(engine, monkeypatch, scripted):
    runs = _search_returning(engine, monkeypatch, ["Python 3.14 is out"])
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "Latest Python release?"}',
        "Python 3.14.",
    ])

    assert len(runs) == 1, f"the same search ran twice: {runs}"


def test_word_order_variant_is_not_re_run(engine, monkeypatch, scripted):
    runs = _search_returning(engine, monkeypatch, ["Python 3.14 is out"])
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "python release latest"}',
        "Python 3.14.",
    ])

    assert len(runs) == 1, f"a reordered query re-ran the search: {runs}"


def test_a_different_question_still_searches(engine, monkeypatch, scripted):
    """Dedupe must not block genuinely new questions."""
    runs = _search_returning(engine, monkeypatch, ["Python 3.14", "Ruby 3.4"])
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest ruby release"}',
        "Python 3.14 and Ruby 3.4.",
    ])

    assert len(runs) == 2


def test_a_failed_search_may_be_retried(engine, monkeypatch, scripted):
    """Dedupe must not trap the agent when the first attempt errored."""
    runs = _search_returning(engine, monkeypatch,
                             ["Error: provider unavailable", "Python 3.14 is out"])
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        "Python 3.14.",
    ])

    assert len(runs) == 2, "a failed search must be retryable"


def test_an_empty_result_may_be_retried(engine, monkeypatch, scripted):
    """No results is not an answer — the agent must be able to try again."""
    runs = _search_returning(engine, monkeypatch, ["", "Python 3.14 is out"])
    _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        "Python 3.14.",
    ])

    assert len(runs) == 2, "an empty result must be retryable"


def test_the_repeat_is_answered_with_the_first_results(engine, monkeypatch, scripted):
    """Blocking the re-run is only useful if the model still gets the results."""
    _search_returning(engine, monkeypatch, ["Python 3.14 is out"])
    model = _drive(engine, monkeypatch, scripted, [
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "latest python release"}',
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "Latest Python release?"}',
        "Python 3.14.",
    ])

    last_turn = model.calls[-1]["messages"]
    assert any("Python 3.14 is out" in str(m.get("content", "")) for m in last_turn)
