"""mem0 is the shipped default engine, so a plain install without the mem0ai package or without an embedding service
uses the native store without announcing a fault. Anything else that stops mem0 is still announced."""
import logging

import pytest

from agent8088 import capabilities, memory


@pytest.fixture(autouse=True)
def _clean():
    memory.reset()
    yield
    memory.reset()


class _Mem0Store:
    last_error = "the mem0ai package is not installed (install it with: agent8088 --memory-setup)"
    quiet = True

    def __init__(self, *a, **k):
        pass

    def available(self):
        return False


def _configure(monkeypatch, tmp_path, store):
    import agent8088.memory.mem0_store as mem0_store
    monkeypatch.setattr(mem0_store, "Mem0MemoryStore", store)
    memory.configure(config={"memory": "1", "memory_engine": "mem0"}, db_path=str(tmp_path / "m.db"))


def test_package_not_installed_falls_back_quietly(monkeypatch, tmp_path, caplog):
    _configure(monkeypatch, tmp_path, _Mem0Store)
    with caplog.at_level(logging.DEBUG):
        assert memory.store() is not None
    entry = capabilities.get(capabilities.MEMORY)
    assert entry.state == capabilities.OK
    assert entry.active == "native" and entry.preferred == "mem0"
    assert "mem0ai package is not installed" in entry.reason and "--memory-setup" in entry.fix
    assert not capabilities.degraded()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_real_mem0_fault_is_still_announced(monkeypatch, tmp_path, caplog):
    class Broken(_Mem0Store):
        last_error = "Qdrant folder is locked by another process"
        quiet = False

    _configure(monkeypatch, tmp_path, Broken)
    with caplog.at_level(logging.DEBUG):
        assert memory.store() is not None
    assert capabilities.get(capabilities.MEMORY).state == capabilities.DEGRADED
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_crash_while_opening_mem0_is_still_announced(monkeypatch, tmp_path):
    class Crashes(_Mem0Store):
        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    _configure(monkeypatch, tmp_path, Crashes)
    assert memory.store() is not None
    assert capabilities.get(capabilities.MEMORY).state == capabilities.DEGRADED


def test_status_still_names_why_mem0_is_not_in_use(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, _Mem0Store)
    health = memory.memory_status()
    assert health["engine"] == "native" and health["ok"] is True
    assert "mem0ai package is not installed" in health["error"]


def test_no_embedding_service_on_the_machine_is_quiet(monkeypatch, tmp_path):
    """The real mem0 store with no Ollama anywhere: quiet, native, and the embedder reports keyword-only."""
    pytest.importorskip("mem0")
    from agent8088.memory import embed
    monkeypatch.setattr(embed, "_reachable", lambda host, port, timeout=0.8: False)
    monkeypatch.setattr(embed, "_ollama_installed", lambda: False)

    class Client:
        base_url = "http://127.0.0.1:11434/v1/"
        embeddings = None

    client = Client()
    client.embeddings = client
    memory.configure(config={"memory": "1", "memory_engine": "mem0", "memory_embed_provider": "ollama"},
                     db_path=str(tmp_path / "m.db"), client_factory=lambda: client,
                     embed_provider="ollama")
    assert memory.store() is not None
    entry = capabilities.get(capabilities.MEMORY)
    assert entry.state == capabilities.OK and entry.active == "native"
    assert not capabilities.degraded()


def test_ollama_installed_but_stopped_is_announced(monkeypatch, tmp_path):
    pytest.importorskip("mem0")
    from agent8088.memory import embed
    monkeypatch.setattr(embed, "_reachable", lambda host, port, timeout=0.8: False)
    monkeypatch.setattr(embed, "_ollama_installed", lambda: True)

    class Client:
        base_url = "http://127.0.0.1:11434/v1/"
        embeddings = None

    client = Client()
    client.embeddings = client
    memory.configure(config={"memory": "1", "memory_engine": "mem0", "memory_embed_provider": "ollama"},
                     db_path=str(tmp_path / "m.db"), client_factory=lambda: client,
                     embed_provider="ollama")
    assert memory.store() is not None
    assert capabilities.get(capabilities.MEMORY).state == capabilities.DEGRADED
