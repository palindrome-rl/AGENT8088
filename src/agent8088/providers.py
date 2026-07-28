"""Provider registry for multi-model support.

Maps provider names to (base_url, api_key). Loaded from config.txt keys:
  provider.<name>.base_url = https://...
  provider.<name>.api_key = <key or env var name>

Model refs use the format: provider:model_name (e.g. "ollama:qwen14b-tooluse-v3").
If no provider prefix, defaults to "ollama".
"""

BUILTIN_PROVIDERS = {
    "ollama":      {"base_url": "http://localhost:11434/v1", "api_key": "ollama"},
    "openrouter":  {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "openai":      {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "anthropic":   {"base_url": "https://api.anthropic.com/v1", "api_key_env": "ANTHROPIC_API_KEY"},
    "gemini":      {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "api_key_env": "GEMINI_API_KEY"},
    "cerebras":    {"base_url": "https://api.cerebras.ai/v1", "api_key_env": "CEREBRAS_API_KEY"},
    "deepseek":    {"base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"},
    "groq":        {"base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "mistral":     {"base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    "moonshot":    {"base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY"},
    "qwen":        {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "DASHSCOPE_API_KEY"},
    "ollama-cloud":{"base_url": "https://ollama.com/v1", "api_key_env": "OLLAMA_API_KEY"},
    "copilot":     {"base_url": "https://api.githubcopilot.com", "api_key_env": "GH_TOKEN"},
}

PROVIDERS = {}

def load_providers(config):
    """Build PROVIDERS from config.txt + BUILTIN_PROVIDERS defaults."""
    PROVIDERS.clear()
    for name, info in BUILTIN_PROVIDERS.items():
        PROVIDERS[name] = dict(info)
    for key, val in config.items():
        if key.startswith("provider.") and key.endswith(".base_url"):
            name = key[len("provider."):-len(".base_url")]
            if name not in PROVIDERS:
                PROVIDERS[name] = {}
            PROVIDERS[name]["base_url"] = val
        elif key.startswith("provider.") and key.endswith(".api_key"):
            name = key[len("provider."):-len(".api_key")]
            if name not in PROVIDERS:
                PROVIDERS[name] = {}
            PROVIDERS[name]["api_key"] = val

def _resolve_api_key(info):
    """Get the API key: direct value first, then env var."""
    if "api_key" in info:
        return info["api_key"]
    env_name = info.get("api_key_env")
    if env_name:
        import os
        return os.environ.get(env_name, "")
    return ""

def get_client_for(model_ref, timeout=120):
    """model_ref = 'provider:model_name' -> (OpenAI client, model_name)."""
    from openai import OpenAI
    if ":" in model_ref:
        provider_name, model_name = model_ref.split(":", 1)
    else:
        provider_name, model_name = "ollama", model_ref
    p = PROVIDERS.get(provider_name)
    if not p:
        raise ValueError(f"Unknown provider: {provider_name}. Available: {', '.join(sorted(PROVIDERS))}")
    api_key = _resolve_api_key(p) or "ollama"
    client = OpenAI(base_url=p.get("base_url", "http://localhost:11434/v1"),
                    api_key=api_key, timeout=timeout)
    return client, model_name

def get_fallback_chain(config):
    """Parse fallback_models config -> list of model refs."""
    raw = config.get("fallback_models", "")
    return [m.strip() for m in raw.split(",") if m.strip()]

def list_providers():
    """Return provider names sorted."""
    return sorted(PROVIDERS.keys())


# Hardcoded fallback model lists per provider (used if /v1/models is unavailable)
FALLBACK_MODELS = {
    "ollama":       ["qwen14b-tooluse-v3", "llama3.3", "qwen2.5-coder:32b"],
    "openrouter":   ["openrouter/auto", "anthropic/claude-sonnet-4", "openai/gpt-4o"],
    "openai":       ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    "anthropic":    ["claude-sonnet-4-6", "claude-haiku-3-5", "claude-opus-4-6"],
    "gemini":       ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-flash"],
    "cerebras":     ["gpt-oss-120b", "gemma-4-31b", "zai-glm-4.7"],
    "deepseek":     ["deepseek-chat", "deepseek-reasoner"],
    "groq":         ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
    "mistral":      ["mistral-small-latest", "mistral-large-latest", "mistral-medium-latest"],
    "moonshot":     ["kimi-k2.6", "moonshot-v1-8k", "moonshot-v1-32k"],
    "qwen":         ["qwen-plus", "qwen-max", "qwen-turbo"],
    "ollama-cloud": ["gpt-oss:120b", "qwen3:14b", "llama3.3"],
    "copilot":      ["gpt-4o", "gpt-4o-mini", "claude-sonnet-4"],
}

_cache = {}

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
    # Gemini's /v1/models returns "models/gemini-2.0-flash" — strip the prefix.
    # Only for gemini — other providers (e.g. Fireworks) use "models/" in real IDs.
    if provider_name == "gemini" and model_id.startswith("models/"):
        return model_id[len("models/"):]
    return model_id


def list_models(provider_name, client=None, timeout=15):
    """Fetch available models from provider's /v1/models endpoint.
    Two-tier cache: in-memory + on-disk (survives restarts). 1-hour TTL.
    Falls back to FALLBACK_MODELS on error. Returns a list of model ID strings."""
    now = time.time()
    disk = _load_disk_cache()
    cached = disk.get(provider_name) or _cache.get(provider_name)
    if cached and (now - cached["ts"]) < 3600:
        return cached["models"]
    if client is None:
        client, _ = get_client_for(f"{provider_name}:", timeout=timeout)
    try:
        resp = client.models.list()
        models = sorted(_normalize_model_id(provider_name, m.id) for m in resp.data)
        entry = {"ts": now, "models": models}
        _cache[provider_name] = entry
        disk[provider_name] = entry
        _save_disk_cache(disk)
        return models
    except Exception:
        return list(FALLBACK_MODELS.get(provider_name, []))