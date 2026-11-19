"""probe_model_vision: asking an endpoint whether its model can see.

Both payload shapes here were captured from live endpoints, not invented:
Ollama's /api/show returns capabilities ["completion", "vision"] for
qwen2.5vl:7b, and OpenRouter's /v1/models returns
architecture.input_modalities ["text", "image", "file"] for gpt-4o.

The three-valued return is the point of the module: True and False are
answers worth caching, None means "the endpoint did not say" and must not be
cached, or one timeout would mark a vision model blind for a week.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import providers


class _Client:
    def __init__(self, base_url, api_key="k"):
        self.base_url = base_url
        self.api_key = api_key


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Never read or write the real ~/.agent8088/models_cache.json, and never
    reach a real endpoint: a strategy this suite forgot to stub would
    otherwise talk to whatever Ollama is running on the developer's machine."""
    monkeypatch.setattr(providers, "_CACHE_FILE", tmp_path / "models_cache.json")

    def _unstubbed(*args, **kwargs):
        raise OSError("unstubbed network call")

    import httpx
    monkeypatch.setattr(httpx, "get", _unstubbed)
    monkeypatch.setattr(httpx, "post", _unstubbed)
    yield


OLLAMA = "http://localhost:11434/v1"
OPENROUTER = "https://openrouter.ai/api/v1"


def _stub_post(monkeypatch, payload, status_code=200, record=None):
    def _post(url, **kwargs):
        if record is not None:
            record.append(url)
        return _Response(payload, status_code)

    import httpx
    monkeypatch.setattr(httpx, "post", _post)


def _stub_get(monkeypatch, payload, status_code=200, record=None):
    def _get(url, **kwargs):
        if record is not None:
            record.append(url)
        return _Response(payload, status_code)

    import httpx
    monkeypatch.setattr(httpx, "get", _get)


# --- Ollama /api/show ------------------------------------------------------

def test_ollama_reports_a_vision_model(monkeypatch):
    _stub_post(monkeypatch, {"capabilities": ["completion", "vision"]})
    assert providers.probe_model_vision(
        _Client(OLLAMA), "qwen2.5vl:7b", "ollama") is True


def test_ollama_reports_a_text_only_model(monkeypatch):
    _stub_post(monkeypatch, {"capabilities": ["completion", "tools"]})
    assert providers.probe_model_vision(
        _Client(OLLAMA), "llama3.3", "ollama") is False


def test_ollama_without_a_capabilities_field_is_inconclusive(monkeypatch):
    # Older Ollama builds predate the field entirely; absent is not "no".
    _stub_post(monkeypatch, {"model_info": {"llama.context_length": 8192}})
    assert providers.probe_model_vision(
        _Client(OLLAMA), "llama3.3", "ollama") is None


def test_ollama_non_200_is_inconclusive(monkeypatch):
    _stub_post(monkeypatch, {"error": "model not found"}, status_code=404)
    assert providers.probe_model_vision(
        _Client(OLLAMA), "nosuchmodel", "ollama") is None


def test_the_ollama_strategy_targets_the_native_path(monkeypatch):
    seen = []
    _stub_post(monkeypatch, {"capabilities": ["vision"]}, record=seen)
    providers.probe_model_vision(_Client(OLLAMA), "qwen2.5vl:7b", "ollama")
    assert seen == ["http://localhost:11434/api/show"]


# --- OpenAI-compatible /v1/models -----------------------------------------

def test_openrouter_image_input_modality_means_vision(monkeypatch):
    _stub_get(monkeypatch, {"data": [
        {"id": "openai/gpt-4o",
         "architecture": {"input_modalities": ["text", "image", "file"]}},
    ]})
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "openai/gpt-4o", "openrouter") is True


def test_openrouter_text_only_input_modality_means_no_vision(monkeypatch):
    _stub_get(monkeypatch, {"data": [
        {"id": "deepseek/deepseek-chat",
         "architecture": {"input_modalities": ["text"]}},
    ]})
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "deepseek/deepseek-chat", "openrouter") is False


def test_a_model_absent_from_the_listing_is_inconclusive(monkeypatch):
    _stub_get(monkeypatch, {"data": [
        {"id": "openai/gpt-4o",
         "architecture": {"input_modalities": ["text", "image"]}},
    ]})
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "some/other-model", "openrouter") is None


def test_a_listing_without_modality_data_is_inconclusive(monkeypatch):
    # Most OpenAI-compatible servers publish nothing useful here.
    _stub_get(monkeypatch, {"data": [{"id": "qwen3.8-27b-rtx3090-long"}]})
    assert providers.probe_model_vision(
        _Client("http://127.0.0.2:8080/v1"), "qwen3.8-27b-rtx3090-long") is None


def test_a_transport_error_is_inconclusive(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    import httpx
    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setattr(httpx, "post", _boom)
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "openai/gpt-4o", "openrouter") is None


# --- caching ---------------------------------------------------------------

def test_a_definite_answer_is_cached_and_the_endpoint_is_asked_once(monkeypatch):
    seen = []
    _stub_get(monkeypatch, {"data": [
        {"id": "openai/gpt-4o",
         "architecture": {"input_modalities": ["text", "image"]}},
    ]}, record=seen)

    first = providers.probe_model_vision(_Client(OPENROUTER), "openai/gpt-4o", "openrouter")
    second = providers.probe_model_vision(_Client(OPENROUTER), "openai/gpt-4o", "openrouter")

    assert (first, second) == (True, True)
    assert len(seen) == 1


def test_a_definite_no_is_cached_too(monkeypatch):
    seen = []
    _stub_get(monkeypatch, {"data": [
        {"id": "deepseek/deepseek-chat",
         "architecture": {"input_modalities": ["text"]}},
    ]}, record=seen)

    providers.probe_model_vision(_Client(OPENROUTER), "deepseek/deepseek-chat", "openrouter")
    providers.probe_model_vision(_Client(OPENROUTER), "deepseek/deepseek-chat", "openrouter")
    assert len(seen) == 1


def test_an_inconclusive_probe_is_not_cached(monkeypatch):
    seen = []
    _stub_get(monkeypatch, {"data": []}, record=seen)

    providers.probe_model_vision(_Client(OPENROUTER), "openai/gpt-4o", "openrouter")
    providers.probe_model_vision(_Client(OPENROUTER), "openai/gpt-4o", "openrouter")
    # A miss must stay retryable rather than sticking for the cache lifetime.
    assert len(seen) == 2


def test_the_cache_is_keyed_per_model(monkeypatch, tmp_path):
    _stub_get(monkeypatch, {"data": [
        {"id": "openai/gpt-4o",
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "deepseek/deepseek-chat",
         "architecture": {"input_modalities": ["text"]}},
    ]})
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "openai/gpt-4o", "openrouter") is True
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "deepseek/deepseek-chat", "openrouter") is False


def test_a_stale_cache_entry_is_re_probed(monkeypatch):
    cache_file = providers._CACHE_FILE
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    # Age every cached entry rather than guessing the implementation's key:
    # probe once, rewrite the timestamps, then probe again.
    _stub_get(monkeypatch, {"data": [
        {"id": "openai/gpt-4o",
         "architecture": {"input_modalities": ["text", "image"]}},
    ]})
    providers.probe_model_vision(_Client(OPENROUTER), "openai/gpt-4o", "openrouter")

    disk = json.loads(cache_file.read_text())
    for entry in disk.values():
        if isinstance(entry, dict) and "ts" in entry:
            entry["ts"] = 0
    cache_file.write_text(json.dumps(disk))

    seen = []
    _stub_get(monkeypatch, {"data": [
        {"id": "openai/gpt-4o",
         "architecture": {"input_modalities": ["text"]}},
    ]}, record=seen)
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "openai/gpt-4o", "openrouter") is False
    assert len(seen) == 1


def test_the_probe_never_raises_on_a_garbage_payload(monkeypatch):
    class _Garbage:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: _Garbage())
    assert providers.probe_model_vision(
        _Client(OPENROUTER), "openai/gpt-4o", "openrouter") is None
