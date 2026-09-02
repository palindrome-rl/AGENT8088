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
            "User works on CAD generation",
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
