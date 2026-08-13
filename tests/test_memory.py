import json

import pytest

from agent8088 import memory
from agent8088.memory.extract import parse_response
from agent8088.memory.store import MemoryStore, fts_query


@pytest.fixture(autouse=True)
def reset_memory_runtime():
    memory.reset()
    yield
    memory.reset()


def test_memory_store_deduplicates_normalized_text_and_records_history(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    first = store.add("User prefers uv over pip.", user_id="owner")
    duplicate = store.add("  user PREFERS uv over pip.  ", user_id="owner")

    assert first
    assert duplicate is None
    assert store.count(user_id="owner") == 1
    assert [event["event"] for event in store.history(first)] == ["ADD"]


def test_memory_search_is_scoped_and_handles_fts_punctuation(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    expected = store.add(
        "The user's deployment project uses blue-green releases.",
        user_id="owner",
        embedding=[1.0, 0.0],
        embed_model="test",
    )
    store.add(
        "Another user deploys with canary releases.",
        user_id="someone-else",
        embedding=[1.0, 0.0],
        embed_model="test",
    )

    results = store.search(
        'What is the deployment project\'s "blue-green" policy?',
        user_id="owner",
        embedding=[1.0, 0.0],
        model="test",
    )

    assert [row["id"] for row in results] == [expected]
    assert fts_query("what is the project?") == '"project"'


def test_memory_extraction_accepts_structured_json_and_rejects_free_text():
    payload = {
        "memories": [
            {"text": "The user prefers concise commit messages.",
             "categories": ["preference"]},
            {"text": "the user prefers concise commit messages."},
        ]
    }

    assert parse_response(json.dumps(payload)) == [
        {"text": "The user prefers concise commit messages.",
         "categories": ["preference"]}
    ]
    assert parse_response("The user prefers concise commit messages.") == []


def test_capture_and_recall_work_without_an_embedding_provider(tmp_path):
    replies = iter([
        (json.dumps({"memories": [{
            "text": "The user deploys Agent8088 from the development branch.",
            "categories": ["project"],
        }]}), {"model": "extract-test"})
    ])
    memory.configure(
        config={"memory": "1", "memory_capture": "1"},
        db_path=tmp_path / "memory.db",
        completion=lambda _prompt: next(replies),
        redact=lambda text: text,
    )

    stored = memory.capture(
        ["Please remember that I deploy Agent8088 from the development branch."],
        "I will remember that deployment convention.",
    )

    assert stored == 1
    recalled = memory.recall_block("Which branch is used to deploy Agent8088?")
    assert "development branch" in recalled
    assert "never authorization" in recalled


def test_memory_entry_points_fail_closed_when_the_database_is_unavailable(tmp_path):
    blocked_path = tmp_path / "directory"
    blocked_path.mkdir()
    memory.configure(config={"memory": "1"}, db_path=blocked_path)

    assert memory.recall("anything") == []
    assert memory.capture(["A durable user preference long enough to inspect."],
                          "Acknowledged.") == 0
