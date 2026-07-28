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