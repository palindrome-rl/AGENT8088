"""Date handling on the way out (the query) and on the way back (the results).

An undated "latest X" query is ranked on popularity, not recency, so a
well-linked old page routinely beats this month's news. And nothing in the
returned snippets tells the model when "now" is, so a past event comes back
looking upcoming.
"""
from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


# --- outbound query augmentation ----------------------------------------

@pytest.mark.parametrize("query", [
    "latest python release",
    "current UK prime minister",
    "who won the most recent world cup",
    "next SpaceX launch",
    "newest iPhone model",
    "recent CVEs in openssl",
])
def test_relative_queries_get_the_year(engine, query):
    assert engine._augment_relative_time_query(query, now=NOW) == f"{query} 2026"


@pytest.mark.parametrize("query", [
    "today's football fixtures",
    "what happened this week in tech",
    "news right now",
])
def test_finer_grained_markers_get_the_month(engine, query):
    assert engine._augment_relative_time_query(query, now=NOW).endswith("August 2026")


@pytest.mark.parametrize("query", [
    "iPhone 2019 reviews",               # already carries a year
    "world cup 1998 final score",        # historical, explicit year
    "who wrote Pride and Prejudice",     # not time-sensitive
    "python list comprehension syntax",  # stable knowledge
    "how does TCP handshake work",       # stable knowledge
])
def test_untouched_queries(engine, query):
    """Never rewrite a query that already names a year or isn't time-sensitive."""
    assert engine._augment_relative_time_query(query, now=NOW) == query


def test_augmentation_can_be_switched_off(engine, monkeypatch):
    monkeypatch.setitem(engine.APP_CONFIG, "search_date_augmentation", "0")

    assert engine._augment_relative_time_query("latest python release", now=NOW) == \
        "latest python release"


def test_augmentation_is_idempotent(engine):
    """Re-augmenting an already-augmented query must not stack years."""
    once = engine._augment_relative_time_query("latest python release", now=NOW)
    twice = engine._augment_relative_time_query(once, now=NOW)

    assert once == twice


# --- the search path uses the augmented query ---------------------------

def _allow_search(engine, monkeypatch):
    monkeypatch.setattr(engine, "_local_searxng_no_prompt_enabled", lambda: True)


def test_search_dispatches_the_augmented_query(engine, monkeypatch):
    sent = {}

    def _run(query, *a, **k):
        sent["query"] = query
        return "1. Python 3.14 released"

    monkeypatch.setattr(engine.web_search, "run_search", _run)
    _allow_search(engine, monkeypatch)

    engine.run_tool("web_search", {"query": "latest python release"})

    assert sent["query"].endswith(str(datetime.now().astimezone().year))


def test_length_guard_sees_the_augmented_query(engine, monkeypatch):
    """A query just under the cap must not slip past it once the year is added.

    The guard has to inspect what actually leaves the machine, not the
    pre-augmentation string.
    """
    _allow_search(engine, monkeypatch)
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: pytest.fail("over-length query was dispatched"))
    long_query = "latest " + ("x" * (engine._WEB_SEARCH_MAX_QUERY_CHARS - 8))

    result = engine.run_tool("web_search", {"query": long_query})

    assert "limited to" in result


def test_secret_guard_sees_the_augmented_query(engine, monkeypatch):
    """Augmentation must not create a path around the credential floor."""
    _allow_search(engine, monkeypatch)
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: pytest.fail("query with a secret was dispatched"))

    result = engine.run_tool("web_search", {"query": "latest sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA"})

    assert result.startswith("Error:")


# --- inbound result framing ---------------------------------------------

def test_results_are_stamped_with_the_retrieval_date(engine):
    framed = engine._frame_search_results("1. Some result", now=NOW)

    assert "2026-08-10" in framed
    assert "1. Some result" in framed
    assert "date" in framed.lower()


def test_framing_is_skipped_for_errors(engine):
    """An error is not a result set — stamping it just buries the message."""
    assert engine._frame_search_results("Error: no provider configured", now=NOW) == \
        "Error: no provider configured"


def test_search_results_reach_the_model_framed(engine, monkeypatch):
    _allow_search(engine, monkeypatch)
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: "1. Python 3.14 released")

    result = engine.run_tool("web_search", {"query": "latest python release"})

    assert "Retrieved" in result
    assert "Python 3.14" in result
