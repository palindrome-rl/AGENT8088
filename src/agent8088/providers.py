"""Provider registry for multi-model support.

Built-in provider base URLs + fallback model lists + /v1/models fetching with disk cache.
The engine (engine.py) has its own load_providers/get_client for runtime use;
this module provides BUILTIN_PROVIDERS (for the --setup wizard) and list_models (for /models).
"""

BUILTIN_PROVIDERS = {
    "ollama":      {"base_url": "http://localhost:11434/v1", "api_key": "ollama"},
    "openrouter":  {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "openai":      {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "gemini":      {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "api_key_env": "GEMINI_API_KEY"},
    "cerebras":    {"base_url": "https://api.cerebras.ai/v1", "api_key_env": "CEREBRAS_API_KEY"},
    "deepseek":    {"base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"},
    "groq":        {"base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "mistral":     {"base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    "moonshot":    {"base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY"},
    "qwen":        {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "DASHSCOPE_API_KEY"},
    "ollama-cloud":{"base_url": "https://ollama.com/v1", "api_key_env": "OLLAMA_API_KEY"},
}

FALLBACK_MODELS = {
    "ollama":       ["qwen14b-tooluse-v3", "llama3.3", "qwen2.5-coder:32b"],
    "openrouter":   ["openrouter/auto", "anthropic/claude-sonnet-4", "openai/gpt-4o"],
    "openai":       ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    "gemini":       ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-flash"],
    "cerebras":     ["gpt-oss-120b", "gemma-4-31b", "zai-glm-4.7"],
    "deepseek":     ["deepseek-chat", "deepseek-reasoner"],
    "groq":         ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
    "mistral":      ["mistral-small-latest", "mistral-large-latest", "mistral-medium-latest"],
    "moonshot":     ["kimi-k2.6", "moonshot-v1-8k", "moonshot-v1-32k"],
    "qwen":         ["qwen-plus", "qwen-max", "qwen-turbo"],
    "ollama-cloud": ["gpt-oss:120b", "qwen3:14b", "llama3.3"],
}

import json, os, time
from pathlib import Path

_CACHE_FILE = Path(os.environ.get("AGENT8088_HOME", str(Path.home() / ".agent8088"))) / "models_cache.json"


def _load_disk_cache():
    try:
        return json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
    except Exception:
        return {}


def _save_disk_cache(d):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(d))
    except Exception:
        pass


def _normalize_model_id(provider_name, model_id):
    if provider_name == "gemini" and model_id.startswith("models/"):
        return model_id[len("models/"):]
    return model_id


def list_models(provider_name, client=None, timeout=15):
    """Fetch available models from provider's /v1/models endpoint.
    Disk-cached for 1 hour. Falls back to FALLBACK_MODELS on error."""
    now = time.time()
    disk = _load_disk_cache()
    cached = disk.get(provider_name)
    if cached and (now - cached["ts"]) < 3600:
        return cached["models"]
    if client is None:
        return list(FALLBACK_MODELS.get(provider_name, []))
    try:
        resp = client.models.list()
        models = sorted(_normalize_model_id(provider_name, m.id) for m in resp.data)
        disk[provider_name] = {"ts": now, "models": models}
        _save_disk_cache(disk)
        return models
    except Exception:
        return list(FALLBACK_MODELS.get(provider_name, []))