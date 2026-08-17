"""Arbitrary user text must never reach FTS5 as syntax.

FTS5 reads `"`, `*`, `(`, `:`, `^`, NEAR and OR as operators, so an unescaped
question mark or apostrophe is enough to turn an ordinary recall into
sqlite3.OperationalError. Every one of these strings is something a user would
plausibly type.
"""
import pytest

from agent8088.memory.store import fts_query

HOSTILE = [
    'what about "uv"?',
    "it's the parser (again)",
    "search * everything",
    "NEAR(a b)",
    "foo OR bar AND baz",
    "col:value",
    "^anchored",
    "unbalanced (paren",
    'unbalanced " quote',
    "emoji 🎯 query",
    "-leading-dash",
    "a - b",
    "100% done",
    "C++ vs C#",
    "why?!?!",
    "path/to/file.py:42",
    "{}[]<>|\\&$#@!~`",
]


@pytest.mark.parametrize("query", HOSTILE)
def test_a_hostile_query_never_raises(store, query):
    store.add("the parser handles uv paths", user_id="owner")
    # Must not raise. Whether it matches is not the point; not crashing is.
    store.search(query, user_id="owner", embedding=[], model="m")


@pytest.mark.parametrize("query", HOSTILE)
def test_a_hostile_query_produces_a_safe_match_expression(query):
    expression = fts_query(query)
    # Every surviving token is a quoted literal, so nothing can be read as an
    # operator. An empty expression is a valid answer: the leg is skipped.
    if expression:
        for part in expression.split(" OR "):
            assert part.startswith('"') and part.endswith('"')
            assert '"' not in part[1:-1]


def test_content_words_survive_the_punctuation_strip():
    assert fts_query('what about "uv"?') == '"uv"'


def test_a_single_character_word_is_dropped_but_a_digit_is_kept():
    """Single letters are noise in BM25; a lone number is often the real query."""
    assert fts_query("a 7") == '"7"'


def test_a_query_of_pure_punctuation_skips_the_keyword_leg(store):
    store.add("a fact", user_id="owner", embedding=[1.0], embed_model="m")
    assert fts_query("?!*") == ""
    assert store._bm25_leg("?!*", user_id="owner") == []


def test_an_apostrophe_cannot_break_out_of_the_sql_string(store):
    """The match expression is a bound parameter, so this is defence in depth."""
    store.add("a fact", user_id="owner")
    store.search("'; DROP TABLE memories; --", user_id="owner", embedding=[], model="m")
    assert store.count(user_id="owner") == 1
