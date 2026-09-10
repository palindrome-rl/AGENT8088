from unittest.mock import MagicMock, patch
import pytest
from agent8088.memory.mem0_store import Mem0MemoryStore

def test_mem0_store_unavailable_when_uninstalled():
    with patch.dict("sys.modules", {"mem0": None}):
        store = Mem0MemoryStore({"path": "/tmp/test_mem0"})
        assert store.available() is False
        assert store.search("test", user_id="alice") == []
        assert store.add("test fact", user_id="alice") is None
        assert store.count(user_id="alice") == 0
        assert store.delete("m1") is False

def test_mem0_store_add_and_search_delegates():
    mock_mem0_cls = MagicMock()
    mock_mem0_instance = MagicMock()
    mock_mem0_cls.from_config.return_value = mock_mem0_instance
    mock_mem0_instance.search.return_value = {
        "results": [
            {"id": "m1", "memory": "Prefers python over go", "score": 0.95}
        ]
    }
    mock_mem0_instance.add.return_value = {"results": [{"id": "m1", "event": "ADD"}]}
    mock_mem0_instance.get_all.return_value = {
        "results": [
            {"id": "m1", "memory": "Prefers python over go"}
        ]
    }

    with patch.dict("sys.modules", {"mem0": MagicMock(Memory=mock_mem0_cls)}):
        store = Mem0MemoryStore({"path": "/tmp/test_mem0", "disable_telemetry": True})
        assert store.available() is True
        
        # Test add
        mid = store.add("Prefers python over go", user_id="alice")
        assert mid == "m1"
        mock_mem0_instance.add.assert_called_once()
        
        # Test search
        hits = store.search("coding preferences", user_id="alice", limit=3)
        assert len(hits) == 1
        assert hits[0]["id"] == "m1"
        assert hits[0]["text"] == "Prefers python over go"
        assert hits[0]["score"] == 0.95
        mock_mem0_instance.search.assert_called_once_with(
            "coding preferences", user_id="alice", limit=3
        )
        
        # Test recent
        recent = store.recent(user_id="alice")
        assert recent == ["Prefers python over go"]
        
        # Test count
        assert store.count(user_id="alice") == 1
