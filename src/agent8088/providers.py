"""Provider registry for multi-model support.

Built-in provider base URLs + fallback model lists + /v1/models fetching with disk cache.
The engine (engine.py) has its own load_providers/get_client for runtime use;
this module provides BUILTIN_PROVIDERS (for the --setup wizard) and list_models (for /models).
"""

BUILTIN_PROVIDERS = {
    "ollama":       {"label": "Ollama (local)", "base_url": "http://localhost:11434/v1", "api_key": "ollama", "default_model": "qwen14b-tooluse-v3"},
    "openrouter":   {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "default_model": "anthropic/claude-sonnet-4"},
    "openai":       {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY", "default_model": "gpt-4o"},
    "gemini":       {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "api_key_env": "GEMINI_API_KEY", "default_model": "gemini-2.0-flash"},
    "cerebras":     {"label": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "api_key_env": "CEREBRAS_API_KEY", "default_model": "gpt-oss-120b"},
    "deepseek":     {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY", "default_model": "deepseek-chat"},
    "groq":         {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY", "default_model": "llama-3.3-70b-versatile"},
    "mistral":      {"label": "Mistral", "base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY", "default_model": "mistral-small-2603"},
    "moonshot":     {"label": "Moonshot (Kimi)", "base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY", "default_model": "kimi-k2.6"},
    "qwen":         {"label": "Qwen (DashScope)", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "DASHSCOPE_API_KEY", "default_model": "qwen-plus"},
    "ollama-cloud": {"label": "Ollama Cloud", "base_url": "https://ollama.com/v1", "api_key_env": "OLLAMA_API_KEY", "default_model": "gpt-oss:120b"},
    "copilot":      {"label": "GitHub Copilot", "base_url": "https://api.githubcopilot.com", "api_key_env": "GH_TOKEN", "default_model": "gpt-4o-mini"},
}
for _name, _provider in BUILTIN_PROVIDERS.items():
    _provider["native_tools"] = _name != "ollama"

def default_provider_name():
    return "ollama"
FALLBACK_MODELS = {
    "ollama":       ["qwen14b-tooluse-v3", "llama3.3", "qwen2.5-coder:32b"],
    "openrouter":   ["anthropic/claude-sonnet-4", "openrouter/auto", "openai/gpt-4o"],
    "openai":       ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    "gemini":       ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-flash"],
    "cerebras":     ["gpt-oss-120b", "gemma-4-31b", "zai-glm-4.7"],
    "deepseek":     ["deepseek-chat", "deepseek-reasoner"],
    "groq":         ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
    "mistral":      ["mistral-small-2603", "mistral-medium-3-5", "mistral-large-2512"],
    "moonshot":     ["kimi-k2.6", "moonshot-v1-8k", "moonshot-v1-32k"],
    "qwen":         ["qwen-plus", "qwen-max", "qwen-turbo"],
    "ollama-cloud": ["gpt-oss:120b", "qwen3:14b", "llama3.3"],
    "copilot":      ["gpt-4o-mini", "gpt-4o", "claude-sonnet-4"],
}

import csv, hashlib, json, os, stat, subprocess, sys, tempfile, time
from pathlib import Path, PureWindowsPath

_CACHE_FILE = Path(os.environ.get("AGENT8088_HOME", str(Path.home() / ".agent8088"))) / "models_cache.json"
MODEL_LIST_TIMEOUT_SECONDS = 5


def _protect_private_file(path: Path) -> None:
    if sys.platform != "win32":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return
    # Absolute path: Git Bash / MSYS shadows Windows' whoami with the coreutils
    # build, which rejects /user — see the matching note in engine.py.
    _system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    _whoami = PureWindowsPath(_system_root) / "System32" / "whoami.exe"
    identity = subprocess.run(
        [str(_whoami), "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, timeout=10,
    )
    try:
        sid = next(csv.reader([identity.stdout]))[1]
    except (IndexError, StopIteration):
        sid = ""
    if identity.returncode or not sid.startswith("S-"):
        raise OSError("Could not determine the current Windows user SID.")
    for acl_args in (["/grant:r", f"*{sid}:(R,W)"], ["/inheritance:r"]):
        result = subprocess.run(
            ["icacls", str(path), *acl_args], capture_output=True, text=True, timeout=10,
        )
        if result.returncode:
            raise OSError(f"Could not protect private file: {path}")


def _load_disk_cache():
    try:
        return json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
    except Exception:
        return {}


def _save_disk_cache(d):
    temporary = None
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=_CACHE_FILE.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(d, stream)
        _protect_private_file(temporary)
        os.replace(temporary, _CACHE_FILE)
    except Exception:
        try:
            if temporary:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _normalize_model_id(provider_name, model_id):
    if provider_name == "gemini" and model_id.startswith("models/"):
        return model_id[len("models/"):]
    return model_id


def builtin_provider_names():
    return list(BUILTIN_PROVIDERS)


def builtin_provider_defaults(name):
    return dict(BUILTIN_PROVIDERS.get(name, {}))


def builtin_provider_label(name):
    return BUILTIN_PROVIDERS.get(name, {}).get("label", name)


def builtin_provider_choice_label(name):
    info = BUILTIN_PROVIDERS[name]
    return f"{info['label']} ({name}) - default: {info['default_model']}"


def list_models(provider_name, client=None, timeout=MODEL_LIST_TIMEOUT_SECONDS, fallback=True):
    """Fetch available models from provider's /v1/models endpoint.
    Disk-cached for 1 hour. Falls back to FALLBACK_MODELS on error."""
    now = time.time()
    disk = _load_disk_cache()
    if client is None:
        return list(FALLBACK_MODELS.get(provider_name, [])) if fallback else []
    endpoint = str(getattr(client, "base_url", ""))
    credential = str(getattr(client, "api_key", ""))
    identity = hashlib.sha256(credential.encode()).hexdigest()[:12] if credential else "anonymous"
    cache_key = f"{provider_name}|{endpoint.rstrip('/')}|{identity}"
    cached = disk.get(cache_key)
    if cached and (now - cached["ts"]) < 3600:
        return cached["models"]
    try:
        fetch_client = client.with_options(timeout=timeout) if hasattr(client, "with_options") else client
        resp = fetch_client.models.list()
        models = sorted(set(_normalize_model_id(provider_name, m.id) for m in resp.data))
        disk[cache_key] = {"ts": now, "models": models}
        _save_disk_cache(disk)
        return models
    except Exception:
        return list(FALLBACK_MODELS.get(provider_name, [])) if fallback else []
