import json
import sqlite3

from agent8088.memory.store import MemoryStore


def test_memory_store_hybrid_lifecycle(tmp_path):
    with MemoryStore(tmp_path / "memory.sqlite3") as store:
        first = store.add(
            "User prefers uv for Python projects",
            user_id="audit",
            embedding=[1.0, 0.0, 0.0],
            embed_model="audit-v1",
            categories=["preference"],
        )
        second = store.add(
            "User works on software generation",
            user_id="audit",
            embedding=[0.0, 1.0, 0.0],
            embed_model="audit-v1",
        )

        assert first and second
        assert store.add("User prefers uv for Python projects", user_id="audit") is None
        results = store.search(
            "Python uv",
            user_id="audit",
            embedding=[1.0, 0.0, 0.0],
            model="audit-v1",
        )
        assert results[0]["id"] == first
        assert store.count(user_id="audit") == 2
        assert store.delete(first) is True
        assert [event["event"] for event in store.history(first)] == ["ADD", "DELETE"]
        assert store.delete_all(user_id="audit") == 1
        assert store.count(user_id="audit") == 0


def test_add_stores_metadata_and_search_get_all_return_it(tmp_path):
    with MemoryStore(tmp_path / "memory.sqlite3") as store:
        memory_id = store.add(
            "User works at Five Rivers Technologies",
            user_id="audit",
            embedding=[1.0, 0.0, 0.0],
            embed_model="audit-v1",
            metadata={"repo": "git@github.com:org/repo.git", "source_channel": "cli"},
        )
        assert memory_id

        [row] = store.get_all(user_id="audit")
        assert json.loads(row["metadata"]) == {
            "repo": "git@github.com:org/repo.git", "source_channel": "cli"}

        [row] = store.search("Five Rivers", user_id="audit",
                             embedding=[1.0, 0.0, 0.0], model="audit-v1")
        assert json.loads(row["metadata"])["source_channel"] == "cli"


def test_add_without_metadata_leaves_it_null(tmp_path):
    with MemoryStore(tmp_path / "memory.sqlite3") as store:
        store.add("no metadata here", user_id="audit")
        [row] = store.get_all(user_id="audit")
        assert row["metadata"] is None


def test_metadata_column_is_added_to_a_pre_existing_database(tmp_path):
    """A store created before this column existed must open without error and
    gain the column, rather than assuming every database already has it."""
    from agent8088.memory.store import _SCHEMA, SCHEMA_VERSION

    db_path = tmp_path / "memory.sqlite3"
    # Build a database with the pre-metadata schema: the real _SCHEMA's
    # CREATE TABLE with the metadata column stripped out, so this test
    # tracks the shipped schema instead of a hand-copied duplicate that
    # would silently drift from it.
    old_schema = _SCHEMA.replace("  metadata         TEXT,\n", "")
    assert "metadata" not in old_schema, "the strip above must have removed it"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(old_schema)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),))
    conn.commit()
    conn.close()

    with MemoryStore(db_path) as store:
        memory_id = store.add("after reopening an old database", user_id="audit",
                              metadata={"source_channel": "web"})
        assert memory_id
        [row] = store.get_all(user_id="audit")
        assert json.loads(row["metadata"]) == {"source_channel": "web"}
