"""Ollama: talk to the configured host, and size to the context it serves.

No real daemon is contacted: urllib/httpx calls are replaced.
"""
import pytest

from agent8088 import local_models as L
from agent8088 import providers


@pytest.fixture(autouse=True)
def _reset_host(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_CONTEXT_LENGTH", raising=False)
    L.set_default_host("")
    yield
    L.set_default_host("")


def test_host_precedence(monkeypatch):
    assert L.resolve_host() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert L.resolve_host() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "gpu-box:11500")
    assert L.resolve_host() == "http://gpu-box:11500"
    L.set_default_host("http://lan-box:11434/v1")
    assert L.resolve_host() == "http://lan-box:11434"
    assert L.resolve_host("http://explicit:1/v1") == "http://explicit:1"


def test_configured_proxy_url_keeps_its_port():
    L.set_default_host("https://ollama.internal/v1")
    assert L.resolve_host() == "https://ollama.internal"


def test_requests_go_to_the_resolved_host(monkeypatch):
    seen = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"models": []}'

    def fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    L.set_default_host("http://lan-box:11434/v1")
    L.list_installed_models()
    assert seen == ["http://lan-box:11434/api/tags"]


def _tags(*names):
    return {"models": [{"name": n, "details": {"family": "bert" if "embed" in n else "qwen3"}}
                       for n in names]}


def test_pick_installed_model(monkeypatch):
    monkeypatch.setattr(L, "_ollama_request", lambda *a, **k: _tags(
        "nomic-embed-text:latest", "qwen3:8b", "llama3.2:latest"))
    assert L.pick_installed_ollama_model(None, "llama3.2") == "llama3.2"
    assert L.pick_installed_ollama_model(None, "qwen14b-tooluse-v3") == "qwen3:8b"


def test_pick_installed_model_keeps_preferred_when_daemon_is_down(monkeypatch):
    def down(*a, **k):
        raise L.OllamaError("Can't reach Ollama")

    monkeypatch.setattr(L, "_ollama_request", down)
    assert L.pick_installed_ollama_model("http://x:1", "qwen14b-tooluse-v3") == "qwen14b-tooluse-v3"


def test_pick_installed_model_with_only_embedders_keeps_preferred(monkeypatch):
    monkeypatch.setattr(L, "_ollama_request", lambda *a, **k: _tags("nomic-embed-text:latest"))
    assert L.pick_installed_ollama_model(None, "llama3.2") == "llama3.2"


class _R:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def test_served_context_prefers_the_loaded_model(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **k: _R(200, {"models": [
        {"name": "qwen3:8b", "context_length": 8192}]}))
    monkeypatch.setattr(httpx, "post", lambda url, **k: pytest.fail("not needed"))
    assert providers.ollama_served_context("http://h:11434/v1", "qwen3:8b") == (8192, "ollama ps")


def test_served_context_reads_a_modelfile_num_ctx(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **k: _R(200, {"models": []}))
    monkeypatch.setattr(httpx, "post", lambda url, **k: _R(200, {
        "parameters": 'num_ctx                        16384\nstop "<|im_end|>"'}))
    assert providers.ollama_served_context("http://h:11434/v1", "m") == (16384, "model num_ctx")


def test_served_context_env_then_unknown(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **k: _R(500, {}))
    monkeypatch.setattr(httpx, "post", lambda url, **k: _R(500, {}))
    monkeypatch.setenv("OLLAMA_CONTEXT_LENGTH", "12000")
    assert providers.ollama_served_context("http://h:11434", "m") == (12000, "OLLAMA_CONTEXT_LENGTH")
    monkeypatch.delenv("OLLAMA_CONTEXT_LENGTH")
    assert providers.ollama_served_context("http://h:11434", "m") == (None, "unknown")


def _probe_as_ollama(engine, monkeypatch, served):
    engine._PROBED_LIMITS.clear()
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "ollama")
    monkeypatch.setattr(engine, "MODEL_NAME", "qwen3:8b")
    monkeypatch.setitem(engine.PROVIDERS, "ollama",
                        {"model": "qwen3:8b", "base_url": "http://localhost:11434/v1"})
    engine.APP_CONFIG.pop("context_window", None)

    class _Client:
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"

    monkeypatch.setattr(engine, "client", _Client())
    monkeypatch.setattr(providers, "probe_model_context_window", lambda *a, **k: (131072, None))
    monkeypatch.setattr(providers, "ollama_served_context", lambda *a, **k: served)
    engine.CONFIG_WARNINGS.clear()
    engine._maybe_probe_context_window()
    return engine.PROVIDERS["ollama"]["context_window"]


def test_engine_sizes_to_the_served_context(engine, monkeypatch):
    assert _probe_as_ollama(engine, monkeypatch, (8192, "model num_ctx")) == "8192"
    assert any("OLLAMA_CONTEXT_LENGTH" in w for w in engine.CONFIG_WARNINGS)


def test_engine_keeps_the_max_but_warns_when_served_is_unknown(engine, monkeypatch):
    assert _probe_as_ollama(engine, monkeypatch, (None, "unknown")) == "131072"
    assert any("silently drops" in w for w in engine.CONFIG_WARNINGS)


def test_ollama_cloud_is_not_capped(engine, monkeypatch):
    assert not providers.is_local_ollama("ollama-cloud", "https://ollama.com/v1")
    assert providers.is_local_ollama("ollama", "")
    assert providers.is_local_ollama("custom", "http://box:11434/v1")
