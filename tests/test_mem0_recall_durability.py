"""Regressions for Mem0 restart, capture lifetime and visible operation failures."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent8088 import memory
from agent8088.memory.mem0_store import Mem0MemoryStore


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    memory.reset()
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path))
    yield
    memory.reset()


def configured_store(tmp_path, monkeypatch, sdk):
    factory = MagicMock()
    factory.from_config.return_value = sdk
    monkeypatch.setattr("agent8088.memory.mem0_store._import_mem0", lambda: factory)
    sdk.get_all.return_value = {"results": []}
    sdk.search.return_value = {"results": []}
    memory.configure(config={"memory": "1", "memory_engine": "mem0"},
                     db_path=str(tmp_path / "memory.db"))
    return memory.store(), factory


def test_saved_fact_survives_older_mem0_reopen(tmp_path, monkeypatch):
    # The older SDK's local-path default resets data at construction. Exercise
    # that boundary, not merely an assertion on the configuration dictionary.
    class OlderMem0:
        @classmethod
        def from_config(cls, config):
            from pathlib import Path
            instance = cls()
            instance.path = Path(config["vector_store"]["config"]["path"]) / "facts.json"
            if not config["vector_store"]["config"].get("on_disk") or not instance.path.exists():
                instance.path.write_text("[]", encoding="utf-8")
            instance.vector_store = SimpleNamespace(client=SimpleNamespace(close=lambda: None))
            return instance

        def add(self, text, *, user_id, metadata, infer):
            rows = json.loads(self.path.read_text())
            rows.append({"id": "fact1", "memory": text, "user_id": user_id})
            self.path.write_text(json.dumps(rows), encoding="utf-8")
            return {"results": [{"id": "fact1", "event": "ADD"}]}

        def get_all(self, *, filters, top_k):
            return {"results": [row for row in json.loads(self.path.read_text())
                                if row["user_id"] == filters["user_id"]][:top_k]}

    monkeypatch.setattr("agent8088.memory.mem0_store._import_mem0", lambda: OlderMem0)
    config = {"path": str(tmp_path / "vectors")}
    first = Mem0MemoryStore(config)
    assert first.add("Synthetic project uses pytest", user_id="qa-owner") == "fact1"
    first.close()
    second = Mem0MemoryStore(config)
    try:
        assert second.recent(user_id="qa-owner") == ["Synthetic project uses pytest"]
        assert second.recent(user_id="another-owner") == []
    finally:
        second.close()


def test_background_capture_keeps_shared_mem0_client_open(tmp_path, monkeypatch):
    sdk = MagicMock()
    active, _ = configured_store(tmp_path, monkeypatch, sdk)
    sdk.add.return_value = {"results": [{"id": "saved", "event": "ADD"}]}
    memory._RUNTIME["completion"] = lambda prompt: (
        '{"memories": [{"text": "Synthetic project uses pytest"}]}', {})
    monkeypatch.setattr(memory, "worth_extracting", lambda exchange: True)
    thread = memory.capture(["Synthetic project uses pytest"], "Understood", in_background=True)
    thread.join(3)
    assert not thread.is_alive()
    assert memory._LAST_CAPTURE["stored"] == 1
    assert active._memory is sdk
    sdk.vector_store.client.close.assert_not_called()
    memory.reset()
    sdk.vector_store.client.close.assert_called_once()


def test_mem0_reload_changes_path_but_unchanged_config_keeps_connection(tmp_path, monkeypatch):
    sdk = MagicMock()
    first, factory = configured_store(tmp_path, monkeypatch, sdk)
    unchanged = {"memory": "1", "memory_engine": "mem0"}
    memory.configure(config=unchanged, db_path=str(tmp_path / "memory.db"))
    assert memory.store() is first
    sdk.search.side_effect = ConnectionError("old store disconnected")
    assert memory.recall("Synthetic fact") == []
    assert not memory.memory_status()["ok"]
    sdk.search.side_effect = None
    memory.configure(config={**unchanged, "memory_mem0_dir": str(tmp_path / "new-vectors")},
                     db_path=str(tmp_path / "memory.db"))
    second = memory.store()
    assert second is not first
    assert second.path == tmp_path / "new-vectors"
    sdk.vector_store.client.close.assert_called_once()
    assert factory.from_config.call_count == 2
    assert memory.memory_status()["ok"]


def test_failed_search_is_reported_and_a_real_empty_result_clears_it(tmp_path, monkeypatch):
    sdk = MagicMock()
    _, _factory = configured_store(tmp_path, monkeypatch, sdk)
    sdk.search.side_effect = ConnectionError("embedding service disconnected")
    assert memory.recall("Synthetic project") == []
    report = memory.memory_status()
    assert not report["ok"]
    assert "embedding service disconnected" in report["error"]
    sdk.search.side_effect = None
    assert memory.recall("No matching facts") == []
    assert memory.memory_status()["ok"]


@pytest.mark.parametrize("failure", [TypeError("internal embedding failure"),
                                     ConnectionError("vector store unavailable")])
def test_failed_add_does_not_retry_with_inference_or_claim_saved(tmp_path, monkeypatch, failure):
    sdk = MagicMock()
    active, _ = configured_store(tmp_path, monkeypatch, sdk)
    sdk.add.side_effect = failure
    assert active.add("Synthetic fact") is None
    sdk.add.assert_called_once()
    assert sdk.add.call_args.kwargs["infer"] is False
    assert not memory.memory_status()["ok"]


def test_empty_add_response_is_not_reported_as_saved(tmp_path, monkeypatch):
    sdk = MagicMock()
    active, _ = configured_store(tmp_path, monkeypatch, sdk)
    sdk.add.return_value = {"results": []}
    assert active.add("Synthetic fact") is None
    assert "did not confirm" in memory.memory_status()["error"]


def test_internal_search_type_error_is_not_retried_as_an_older_api(tmp_path, monkeypatch):
    sdk = MagicMock()
    active, _ = configured_store(tmp_path, monkeypatch, sdk)
    sdk.search.side_effect = TypeError("internal vector decoding failure")
    assert active.search("Synthetic fact") == []
    sdk.search.assert_called_once()
    assert "vector decoding" in active.operation_error


def test_legacy_search_and_get_all_keep_the_requested_user_scope(tmp_path, monkeypatch):
    sdk = MagicMock()
    active, _ = configured_store(tmp_path, monkeypatch, sdk)
    scopes = []

    def search(query, *, user_id, limit=5):
        scopes.append((user_id, limit))
        return {"results": [{"id": "legacy", "memory": "Synthetic fact", "score": 1}]}

    def get_all(*, user_id):
        scopes.append((user_id, None))
        return [{"id": "legacy", "memory": "Synthetic fact"}]

    sdk.search = search
    sdk.get_all = get_all
    assert active.search("Synthetic fact", user_id="qa-owner", limit=3)[0]["id"] == "legacy"
    assert active.recent(user_id="qa-owner") == ["Synthetic fact"]
    assert scopes == [("qa-owner", 3), ("qa-owner", None)]


def test_mem0_specific_model_is_the_one_preflight_probes(tmp_path, monkeypatch):
    sdk_factory = MagicMock()
    monkeypatch.setattr("agent8088.memory.mem0_store._import_mem0", lambda: sdk_factory)
    models = []

    class Client:
        embeddings = None

        def __init__(self):
            self.embeddings = self
            self.base_url = "http://127.0.0.1:11434/v1"

        def create(self, *, model, input, timeout):
            models.append(model)
            raise ConnectionError("specific model missing")

    native_probe = SimpleNamespace(model="nomic-embed-text", available=lambda: True)
    active = Mem0MemoryStore({"path": str(tmp_path / "vectors"),
                             "embed_model": "specific-model"},
                            client_factory=Client, embedder=native_probe)
    assert not active.available()
    assert models == ["specific-model"]
    assert "ollama/specific-model" in active.last_error
    sdk_factory.from_config.assert_not_called()
