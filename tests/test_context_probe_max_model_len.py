"""probe_model_context_window must read vLLM's own field name.

vLLM's /v1/models publishes the context window as `max_model_len`, not
`context_length`/`context_window` (OpenRouter/Groq's names) -- without this
the probe misses on vLLM and agent8088 falls back to the conservative
32768-token default regardless of --max-model-len."""
import types


def _client_with_model(**fields):
    model = types.SimpleNamespace(id="qwen3.8-27b", **fields)
    resp = types.SimpleNamespace(data=[model])
    client = types.SimpleNamespace(
        base_url="http://127.0.0.1:8000/v1", api_key="",
        models=types.SimpleNamespace(list=lambda: resp))
    return client


def test_probe_reads_vllm_max_model_len():
    from agent8088.providers import probe_model_context_window

    client = _client_with_model(max_model_len=65536)
    ctx, _out = probe_model_context_window(client, "qwen3.8-27b", provider_name="vllm")
    assert ctx == 65536


def test_standard_fields_still_win_when_both_present():
    """context_length (OpenRouter/Groq's field) is tried first; a provider
    publishing both is not expected, but the order must stay deterministic."""
    from agent8088.providers import probe_model_context_window

    client = _client_with_model(context_length=131072, max_model_len=65536)
    ctx, _out = probe_model_context_window(client, "qwen3.8-27b", provider_name="vllm")
    assert ctx == 131072
