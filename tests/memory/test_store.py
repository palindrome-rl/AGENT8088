"""Schema, CRUD and the guarantees the database itself is asked to enforce."""
import os
import sqlite3
import stat
import sys

import pytest

from agent8088.memory.store import MemoryStore, fts_query, normalise, text_hash


def test_schema_is_created_on_first_connect(store):
    tables = {row[0] for row in store.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"memories", "vectors", "memory_events", "meta"} <= tables


def test_reopening_an_existing_store_does_not_lose_memories(db_path):
    with MemoryStore(db_path) as first:
        first.add("user prefers uv over pip", user_id="owner")
    with MemoryStore(db_path) as second:
        assert [row["text"] for row in second.get_all(user_id="owner")] == [
            "user prefers uv over pip"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_new_database_is_owner_only(db_path):
    with MemoryStore(db_path) as opened:
        opened.add("a fact", user_id="owner")
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600


def test_duplicate_text_is_rejected_by_the_database(store):
    first = store.add("the repo has no CI", user_id="owner")
    second = store.add("the repo has no CI", user_id="owner")
    assert first
    assert second is None
    assert store.count(user_id="owner") == 1


def test_dedup_ignores_case_and_whitespace(store):
    store.add("Prefers uv over pip", user_id="owner")
    assert store.add("  prefers   UV over pip  ", user_id="owner") is None
    assert store.count(user_id="owner") == 1


def test_the_same_fact_for_two_users_is_not_a_duplicate(store):
    assert store.add("prefers uv", user_id="alice")
    assert store.add("prefers uv", user_id="bob")
    assert store.count(user_id="alice") == 1
    assert store.count(user_id="bob") == 1


def test_unique_constraint_survives_a_bug_in_the_caller(store):
    """The dedup guarantee is the constraint, not a prior SELECT: a caller that
    inserts directly must still be refused."""
    store.add("prefers uv", user_id="owner")
    with pytest.raises(sqlite3.IntegrityError):
        store.connect().execute(
            "INSERT INTO memories (id, user_id, text, hash, source, created_at, updated_at)"
            " VALUES ('x','owner','prefers uv',?, 'extracted', 0, 0)",
            (text_hash("prefers uv"),))


def test_fts_index_tracks_inserts(store):
    store.add("the sandbox backend is docker", user_id="owner")
    assert store._bm25_leg("docker", user_id="owner")


def test_fts_index_tracks_deletes(store):
    memory_id = store.add("the sandbox backend is docker", user_id="owner")
    store.delete(memory_id)
    assert store._bm25_leg("docker", user_id="owner") == []


def test_fts_index_tracks_updates(store):
    memory_id = store.add("the sandbox backend is docker", user_id="owner")
    store.connect().execute("UPDATE memories SET text='the sandbox backend is podman'"
                            " WHERE id=?", (memory_id,))
    store.connect().commit()
    assert store._bm25_leg("podman", user_id="owner")
    assert store._bm25_leg("docker", user_id="owner") == []


def test_deleting_a_memory_removes_its_vector(store):
    memory_id = store.add("a fact", user_id="owner", embedding=[1.0, 0.0],
                          embed_model="m")
    store.delete(memory_id)
    assert store.connect().execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0


def test_search_cannot_reach_another_users_memories(store):
    store.add("alice runs postgres locally", user_id="alice",
              embedding=[1.0, 0.0], embed_model="m")
    found = store.search("postgres", user_id="bob", embedding=[1.0, 0.0], model="m")
    assert found == []


def test_delete_all_is_scoped_to_one_user(store):
    store.add("alice fact", user_id="alice")
    store.add("bob fact", user_id="bob")
    assert store.delete_all(user_id="alice") == 1
    assert store.count(user_id="alice") == 0
    assert store.count(user_id="bob") == 1


def test_events_record_the_full_lifecycle(store):
    memory_id = store.add("a fact", user_id="owner")
    store.delete(memory_id)
    events = [row["event"] for row in store.history(memory_id)]
    assert events == ["ADD", "DELETE"]


def test_recent_is_scoped_to_a_run(store):
    store.add("from this session", user_id="owner", run_id="run-1")
    store.add("from another session", user_id="owner", run_id="run-2")
    assert store.recent(user_id="owner", run_id="run-1") == ["from this session"]


def test_stale_vectors_are_counted_not_compared(store):
    store.add("old model fact", user_id="owner", embedding=[1.0, 0.0],
              embed_model="old-embed")
    assert store.stale_vector_count(model="new-embed") == 1


def test_a_vector_from_another_model_is_excluded_from_the_vector_leg(store):
    store.add("old model fact", user_id="owner", embedding=[1.0, 0.0],
              embed_model="old-embed")
    assert store._vector_leg([1.0, 0.0], user_id="owner", model="new-embed") == []


def test_a_vector_of_the_wrong_dimension_is_skipped(store):
    """Same model name, different output width: comparing truncated vectors would
    return a confident wrong score, so the row is skipped instead."""
    store.add("a fact", user_id="owner", embedding=[1.0, 0.0], embed_model="m")
    assert store._vector_leg([1.0, 0.0, 0.0], user_id="owner", model="m") == []


def test_empty_text_is_not_stored(store):
    assert store.add("   ", user_id="owner") is None
    assert store.count(user_id="owner") == 0


def test_normalise_leaves_a_zero_vector_alone():
    assert normalise([0.0, 0.0]) == [0.0, 0.0]


def test_normalise_produces_unit_length():
    length = sum(component ** 2 for component in normalise([3.0, 4.0])) ** 0.5
    assert abs(length - 1.0) < 1e-6


def test_access_is_recorded_for_returned_memories(store):
    store.add("prefers uv", user_id="owner")
    store.search("uv", user_id="owner")
    row = store.get_all(user_id="owner")[0]
    assert row["access_count"] == 1
    assert row["last_accessed_at"]


def test_fts_query_drops_syntax_and_keeps_content_words():
    assert fts_query('what about "uv" (and pip)?') == '"uv" OR "pip"'


def test_fts_query_drops_stopwords():
    """The tokens are OR-ed, so one shared stopword would make any query match any
    memory -- and on a small store BM25 has nothing better to rank."""
    assert fts_query("what is the capital of France") == '"capital" OR "France"'


def test_a_query_of_only_stopwords_carries_no_keyword_signal():
    assert fts_query("what is it about") == ""


def test_fts_query_is_empty_when_nothing_survives():
    assert fts_query("?? ! *") == ""
