"""Memory fallbacks are reported to the capabilities registry: mem0 -> native,
a store that cannot open, an embedder down (keyword-only recall), and the
split brain of facts saved natively while mem0 was requested. The tool
selection index is rebuilt once the embedder recovers."""

import pytest

from agent8088 import capabilities, memory
from agent8088.memory.embed import Embedder


@pytest.fixture(autouse=True)
def _clean():
    memory.reset()
    yield
    memory.reset()


class _Unavailable:
    last_error = "mem0ai is not installed"

    def __init__(self, *a, **k):
        pass

    def available(self):
        return False


def _mem0_down(monkeypatch, tmp_path):
    import agent8088.memory.mem0_store as mem0_store
    monkeypatch.setattr(mem0_store, "Mem0MemoryStore", _Unavailable)
    memory.configure(config={"memory": "1", "memory_engine": "mem0"},
                     db_path=str(tmp_path / "m.db"))


def test_mem0_fallback_reports_memory_degraded(monkeypatch, tmp_path):
    _mem0_down(monkeypatch, tmp_path)
    assert memory.store() is not None
    entry = capabilities.get(capabilities.MEMORY)
    assert entry.state == capabilities.DEGRADED
    assert entry.active == "native" and entry.preferred == "mem0"
    assert "mem0ai is not installed" in entry.reason
    assert "--memory-setup" in entry.fix


def test_missing_ollama_embeddings_falls_back_with_specific_diagnosis(monkeypatch, tmp_path):
    import agent8088.memory.mem0_store as mem0_store

    class _Mem0:
        @classmethod
        def from_config(cls, _config):
            return cls()

    class _Embedder:
        model = "nomic-embed-text"
        last_error = "connection refused"

        def available(self):
            return False

    monkeypatch.setattr(mem0_store, "_import_mem0", lambda: _Mem0)
    monkeypatch.setattr(memory, "embedder", lambda: _Embedder())
    memory.configure(config={"memory": "1", "memory_engine": "mem0",
                             "memory_mem0_embed_provider": "ollama"},
                     db_path=str(tmp_path / "m.db"))

    status = memory.memory_status()

    assert status["engine"] == "native" and status["ok"] is True
    assert "embedding service unavailable for ollama/nomic-embed-text" in status["error"]
    assert "connection refused" in status["error"]
    assert "mem0's memories are not recalled" in capabilities.get(capabilities.MEMORY).impact


def test_native_by_choice_is_ok(tmp_path):
    memory.configure(config={"memory": "1"}, db_path=str(tmp_path / "m.db"))
    memory.store()
    assert capabilities.get(capabilities.MEMORY).state == capabilities.OK


def test_unopenable_store_reports_unavailable(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    memory.configure(config={"memory": "1"}, db_path=str(blocker / "sub" / "m.db"))
    assert memory.store() is None
    entry = capabilities.get(capabilities.MEMORY)
    assert entry.state == capabilities.UNAVAILABLE
    assert "memory off" in entry.impact


def test_reset_forgets_memory_capabilities(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    memory.configure(config={"memory": "1"}, db_path=str(blocker / "sub" / "m.db"))
    memory.store()
    memory.reset()
    assert capabilities.get(capabilities.MEMORY) is None


def test_split_brain_is_counted(monkeypatch, tmp_path):
    _mem0_down(monkeypatch, tmp_path)
    memory.configure(config={"memory": "1", "memory_engine": "mem0", "memory_capture": "1"},
                     db_path=str(tmp_path / "m.db"),
                     completion=lambda prompt: ('{"memories": [{"text": "User prefers tabs"}]}', {}))
    monkeypatch.setattr(memory, "worth_extracting", lambda exchange: True)
    monkeypatch.setattr(memory, "parse_response",
                        lambda raw, max_memories=3: [{"text": "User prefers tabs"}])
    stored = memory.capture(["I always use tabs"], "noted")
    assert stored == 1
    entry = capabilities.get(capabilities.MEMORY)
    assert "1 memories saved to the native store while mem0 was down" in entry.impact
    assert "/memory status" in entry.impact


# --- embedder ---------------------------------------------------------------

class _Client:
    def __init__(self, ok):
        self.ok = ok

    @property
    def embeddings(self):
        return self

    def create(self, model, input, timeout=None):
        if not self.ok[0]:
            raise ConnectionError("connection refused")
        item = type("E", (), {"embedding": [1.0, 0.0]})()
        return type("R", (), {"data": [item for _ in input]})()


def test_embedder_failure_reports_keyword_only_and_recovers():
    ok = [False]
    embedder = Embedder(lambda: _Client(ok), "nomic-embed-text", breaker_seconds=0)
    assert embedder.embed(["hi"]) == []
    entry = capabilities.get(capabilities.MEMORY_EMBED)
    assert entry.state == capabilities.DEGRADED
    assert entry.active == "keyword-only (BM25)"
    assert "semantic matches missed" in entry.impact
    assert "ollama pull nomic-embed-text" in entry.fix
    ok[0] = True
    assert embedder.embed(["hi"]) == [[1.0, 0.0]]
    assert capabilities.get(capabilities.MEMORY_EMBED).state == capabilities.OK


def test_embedder_ready_respects_breaker():
    embedder = Embedder(lambda: _Client([False]), "m", breaker_seconds=300)
    assert embedder.ready()
    embedder.embed(["hi"])
    assert not embedder.ready()


def test_tool_index_rebuilt_after_embedder_recovers(engine, monkeypatch):
    class _Flaky:
        def __init__(self):
            self.up = False
            self.calls = 0

        def ready(self):
            return True

        def embed(self, texts):
            self.calls += 1
            return [[1.0] for _ in texts] if self.up else []

    flaky = _Flaky()
    monkeypatch.setattr(engine.memory, "embedder", lambda: flaky)
    engine._TOOL_INDEX_CACHE.clear()
    names = set(list(engine.TOOL_SPECS)[:3])
    assert engine._tool_selection_index(names)[2] == []
    flaky.up = True
    assert engine._tool_selection_index(names)[2]  # not stuck on the empty snapshot
    calls = flaky.calls
    engine._tool_selection_index(names)
    assert flaky.calls == calls  # a good snapshot stays cached


def test_tool_index_empty_snapshot_kept_while_breaker_open(engine, monkeypatch):
    class _Down:
        calls = 0

        def ready(self):
            return False

        def embed(self, texts):
            self.calls += 1
            return []

    down = _Down()
    monkeypatch.setattr(engine.memory, "embedder", lambda: down)
    engine._TOOL_INDEX_CACHE.clear()
    names = set(list(engine.TOOL_SPECS)[:3])
    engine._tool_selection_index(names)
    engine._tool_selection_index(names)
    assert down.calls == 1
