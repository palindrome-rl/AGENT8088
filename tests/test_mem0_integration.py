from unittest.mock import MagicMock, patch
from agent8088 import memory
import pytest


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    memory.reset()
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    yield
    memory.reset()

def test_recall_block_with_mem0_engine(tmp_path):
    mock_store = MagicMock()
    mock_store.available.return_value = True
    mock_store.search.return_value = [{"id": "1", "text": "User is building Agent8088"}]
    
    with patch("agent8088.memory.mem0_store.Mem0MemoryStore", return_value=mock_store):
        memory.configure(config={"memory": "1", "memory_engine": "mem0"}, db_path=str(tmp_path / "memory.db"))
        block = memory.recall_block("What am I building?", identity="owner")
        assert "User is building Agent8088" in block
        assert "## Recalled context" in block

def test_capture_with_mem0_engine(tmp_path):
    mock_store = MagicMock()
    mock_store.available.return_value = True
    mock_store.recent.return_value = []
    mock_store.add.return_value = "id1"
    
    def fake_completion(prompt):
        return ('{"memories": [{"text": "Prefers pytest over unittest", "categories": ["testing"]}]}', {"total_tokens": 50})
    
    with patch("agent8088.memory.mem0_store.Mem0MemoryStore", return_value=mock_store):
        memory.configure(
            config={"memory": "1", "memory_engine": "mem0", "memory_capture": "1"},
            completion=fake_completion,
            db_path=str(tmp_path / "memory.db"),
        )
        count = memory._capture(
            ["I always prefer using pytest over unittest for all Python test suites."],
            "Understood, I will remember to write pytest tests.",
            identity="owner",
        )
        assert count >= 1
        assert mock_store.add.called

def test_mem0_fallback_to_native_when_unavailable(tmp_path):
    mock_mem0_store = MagicMock()
    mock_mem0_store.available.return_value = False
    
    with patch("agent8088.memory.mem0_store.Mem0MemoryStore", return_value=mock_mem0_store):
        memory.configure(config={"memory": "1", "memory_engine": "mem0"}, db_path=str(tmp_path / "memory.db"))
        st = memory.store()
        # Should fall back to native MemoryStore
        assert isinstance(st, memory.MemoryStore)
