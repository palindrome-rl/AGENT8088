from unittest.mock import MagicMock, patch
import pytest
from agent8088.memory.mem0_store import Mem0MemoryStore


def _fake_mem0():
    cls = MagicMock()
    cls.from_config.return_value = MagicMock()
    return cls


def _llm_section(fake_cls):
    return fake_cls.from_config.call_args[0][0].get("llm")


# --- the LLM provider name mem0 is actually given -------------------------
# mem0 validates this against its own registry and refuses anything outside
# it. agent8088's provider names are its own, so passing them through killed
# the whole engine with "Unsupported LLM provider: custom" -- and because the
# fallback to native was silent, it looked exactly like mem0ai not being
# installed. Observed on three machines before it was diagnosed.


@pytest.mark.parametrize("agent_provider", [
    "custom", "cerebras", "mistral", "moonshot", "openrouter", "qwen",
])
def test_openai_compatible_providers_are_mapped_to_openai(agent_provider):
    fake = _fake_mem0()
    client = MagicMock(api_key="sk-local", base_url="http://127.0.0.1:8080/v1")
    store = Mem0MemoryStore({"path": "/tmp/test_mem0", "llm_provider": agent_provider},
                            client_factory=lambda: client)
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        assert store.available() is True
    section = _llm_section(fake)
    assert section["provider"] == "openai"
    # Aimed at the user's own endpoint, not api.openai.com.
    assert section["config"]["openai_base_url"] == "http://127.0.0.1:8080/v1"
    assert store.llm_info == {"requested": agent_provider, "resolved": "openai"}


@pytest.mark.parametrize("provider", ["anthropic", "gemini", "groq", "deepseek", "ollama"])
def test_providers_mem0_knows_natively_are_passed_through(provider):
    fake = _fake_mem0()
    client = MagicMock(api_key="k", base_url="http://host/v1")
    store = Mem0MemoryStore({"path": "/tmp/test_mem0", "llm_provider": provider},
                            client_factory=lambda: client)
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        assert store.available() is True
    section = _llm_section(fake)
    assert section["provider"] == provider
    # openai_base_url is meaningless to these and must not be smuggled in.
    if provider != "ollama":
        assert "openai_base_url" not in section["config"]


def test_ollama_still_gets_its_own_base_url():
    fake = _fake_mem0()
    store = Mem0MemoryStore({"path": "/tmp/test_mem0", "llm_provider": "ollama"})
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        assert store.available() is True
    assert "ollama_base_url" in _llm_section(fake)["config"]


def test_configured_ollama_endpoint_beats_server_bind_address(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    fake = _fake_mem0()
    client = MagicMock(api_key="none", base_url="http://127.0.0.1:11434/v1")
    store = Mem0MemoryStore({"path": "/tmp/test_mem0", "embed_provider": "ollama"},
                            client_factory=lambda: client)
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        assert store.available() is True
    config = fake.from_config.call_args[0][0]
    assert config["embedder"]["config"]["ollama_base_url"] == "http://127.0.0.1:11434"


def test_non_ollama_embedder_does_not_use_its_endpoint_for_ollama_llm(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.internal:11434")
    fake = _fake_mem0()
    client = MagicMock(api_key="none", base_url="http://embeddings.internal/v1")
    store = Mem0MemoryStore({"path": "/tmp/test_mem0", "embed_provider": "openai",
                             "llm_provider": "ollama"}, client_factory=lambda: client)
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        assert store.available() is True
    assert _llm_section(fake)["config"]["ollama_base_url"] == "http://ollama.internal:11434"


def test_close_releases_embedded_qdrant_client():
    fake = _fake_mem0()
    store = Mem0MemoryStore({"path": "/tmp/test_mem0"})
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        assert store.available() is True
    store.close()
    fake.from_config.return_value.vector_store.client.close.assert_called_once_with()
    assert store._memory is None


# --- the failure must say why --------------------------------------------


def test_init_failure_records_a_reason():
    """A silent fallback is what let the provider bug survive three machines."""
    fake = _fake_mem0()
    fake.from_config.side_effect = ValueError("Unsupported LLM provider: nope")
    store = Mem0MemoryStore({"path": "/tmp/test_mem0", "llm_provider": "x"})
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        assert store.available() is False
    assert "Unsupported LLM provider: nope" in store.last_error
    assert "Unsupported LLM provider: nope" in store.status()["error"]


def test_missing_package_is_distinguishable_from_a_failed_init():
    store = Mem0MemoryStore({"path": "/tmp/test_mem0"})
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=None):
        assert store.available() is False
    assert "not installed" in store.last_error


def test_add_merges_caller_metadata_into_mem0s_own_dict():
    """repo/source_channel must ride alongside project/agent_id/run_id/source,
    not replace them -- mem0's own bookkeeping fields still need to reach mem0."""
    fake = _fake_mem0()
    store = Mem0MemoryStore({"path": "/tmp/test_mem0"})
    with patch("agent8088.memory.mem0_store._import_mem0", return_value=fake):
        store.add("fact", user_id="owner", project="/repo", run_id="s1",
                  metadata={"repo": "git@github.com:org/repo.git", "source_channel": "cli"})
    call_kwargs = fake.from_config.return_value.add.call_args.kwargs
    sent = call_kwargs["metadata"]
    assert sent["project"] == "/repo"
    assert sent["run_id"] == "s1"
    assert sent["repo"] == "git@github.com:org/repo.git"
    assert sent["source_channel"] == "cli"


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
            "coding preferences", filters={"user_id": "alice"}, top_k=3
        )
        
        # Test recent
        recent = store.recent(user_id="alice")
        assert recent == ["Prefers python over go"]
        
        # Test count
        assert store.count(user_id="alice") == 1
