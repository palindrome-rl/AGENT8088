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
    "anthropic":    {"label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1/", "api_key_env": "ANTHROPIC_API_KEY", "default_model": "claude-sonnet-4-6"},
}
for _name, _provider in BUILTIN_PROVIDERS.items():
    _provider["native_tools"] = _name != "ollama"

def default_provider_name():
    return "ollama"

# Providers that return live rate-limit headers on every inference response
# (verified against each vendor's current docs) -- everything else either
# needs a separate call (OpenRouter's /key), only exposes a currency balance
# (DeepSeek, Moonshot), or nothing at all without an admin/management key
# (Mistral, Gemini, Qwen) or has no quota concept (Ollama, custom). Field
# names: request/token remaining+limit header, plus a reset header. Reset
# format differs per provider (RFC3339 timestamp for Anthropic, a duration
# string like "6m0s" for OpenAI, seconds for Groq) -- parsed accordingly in
# engine.py, not normalized here.
#
# Cerebras is genuinely Tier 1 but its docs disagreed on whether requests and
# tokens get separate headers or share one pair -- until verified live,
# engine.py treats its single Remaining/Limit/Reset triplet as tokens-only.
RATE_LIMIT_HEADERS = {
    "anthropic": {
        "requests_remaining": "anthropic-ratelimit-requests-remaining",
        "requests_limit": "anthropic-ratelimit-requests-limit",
        "tokens_remaining": "anthropic-ratelimit-tokens-remaining",
        "tokens_limit": "anthropic-ratelimit-tokens-limit",
        "reset": "anthropic-ratelimit-tokens-reset",
        "reset_format": "rfc3339",
    },
    "openai": {
        "requests_remaining": "x-ratelimit-remaining-requests",
        "requests_limit": "x-ratelimit-limit-requests",
        "tokens_remaining": "x-ratelimit-remaining-tokens",
        "tokens_limit": "x-ratelimit-limit-tokens",
        "reset": "x-ratelimit-reset-tokens",
        "reset_format": "duration",
    },
    "groq": {
        "requests_remaining": "x-ratelimit-remaining-requests",
        "requests_limit": "x-ratelimit-limit-requests",
        "tokens_remaining": "x-ratelimit-remaining-tokens",
        "tokens_limit": "x-ratelimit-limit-tokens",
        "reset": "x-ratelimit-reset-tokens",
        "reset_format": "seconds",
    },
    "cerebras": {
        "requests_remaining": None,
        "requests_limit": None,
        "tokens_remaining": "x-ratelimit-remaining",
        "tokens_limit": "x-ratelimit-limit",
        "reset": "x-ratelimit-reset",
        "reset_format": "seconds",
    },
}
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
    "anthropic":    ["claude-sonnet-4-6", "claude-opus-5", "claude-haiku-3.5"],
}


def resolve_subagent_model(raw: str, provider: str, client=None) -> tuple[str, str]:
    """Return (model_id, warning). Empty model_id means "use the session model".

    Subagents run on the active provider only. A model must be one the provider
    actually offers; anything else falls back to the session model with a warning.
    """
    raw = (raw or "").strip()
    if not raw or raw.lower() == "inherit":
        return "", ""
    if ":" in raw and raw.split(":", 1)[0] in BUILTIN_PROVIDERS:
        return "", (f"cross-provider model '{raw}' is no longer supported; "
                    f"using the session model")
    available = list_models(provider, client=client, fallback=False)
    if available and raw not in available:
        return "", (f"model '{raw}' is not available on {provider}; "
                    f"using the session model")
    return raw, ""


# OpenAI-compatible /v1/models responses normally expose only an id, owner and
# creation time. Context and output limits are NOT standardized there. Instead,
# probe_model_context_window queries the provider's native model-info endpoint
# (Ollama's /api/show for ollama/ollama-cloud, /v1/models for others) and reads
# whatever context-length field the endpoint publishes. Provider-specific config
# values (provider.<name>.context_window) remain the escape hatch for private
# endpoints and take precedence over the probe in engine._active_model_token_limits().


def model_token_limits(provider_name, model_id):
    """Return reviewed limits for one exact provider/model pairing.

    No hardcoded catalog — limits are probed from the endpoint at runtime via
    probe_model_context_window. This function is kept as a stable import target
    for engine._active_model_token_limits but always returns empty: the probe
    writes session-only values into PROVIDERS[name] directly.
    """
    return {}

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
        # max_retries=0: a model listing that fails is a miss the caller
        # already handles, not something to retry. The SDK default of 2
        # retries multiplies the timeout by three on a dead endpoint.
        fetch_client = (client.with_options(timeout=timeout, max_retries=0)
                        if hasattr(client, "with_options") else client)
        resp = fetch_client.models.list()
        models = sorted(set(_normalize_model_id(provider_name, m.id) for m in resp.data))
        disk[cache_key] = {"ts": now, "models": models}
        _save_disk_cache(disk)
        return models
    except Exception:
        return list(FALLBACK_MODELS.get(provider_name, [])) if fallback else []


def probe_model_context_window(client, model_id, provider_name="", timeout=MODEL_LIST_TIMEOUT_SECONDS):
    """Best-effort: ask the endpoint for the model's context window and output limit.

    Returns (context_window, max_completion_tokens) or (None, None) on miss.
    Never raises — the caller treats None as "use fallback".

    Strategies tried in order, by provider:
    1. Ollama native /api/show — ollama/ollama-cloud publish model_info with
       "<arch>.context_length". The reliable path for Ollama providers.
    2. Google native /v1beta/models/{id} — gemini publishes inputTokenLimit
       and outputTokenLimit that the OpenAI-compatible layer doesn't expose.
    3. OpenAI-compatible /v1/models — OpenRouter, Groq, and others add
       non-standard fields (context_length, context_window, top_provider).
       Most OpenAI endpoints don't publish anything here.
    """
    base_url = str(getattr(client, "base_url", "")).rstrip("/")
    api_key = str(getattr(client, "api_key", ""))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    # Strategy 1: Ollama /api/show
    if provider_name in ("ollama", "ollama-cloud") or "ollama" in base_url:
        try:
            import httpx
            show_url = base_url.replace("/v1", "") + "/api/show"
            r = httpx.post(show_url, json={"model": model_id},
                           headers=headers, timeout=timeout)
            if r.status_code == 200:
                info = r.json().get("model_info", {})
                ctx = None
                for key, value in info.items():
                    if key.endswith("context_length") and isinstance(value, int):
                        ctx = value
                        break
                if ctx:
                    return ctx, None
        except Exception:
            pass

    # Strategy 2: Google native API (gemini)
    if provider_name == "gemini" or "googleapis" in base_url:
        try:
            import httpx
            model_clean = model_id.replace("models/", "")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_clean}"
            r = httpx.get(url, headers={"x-goog-api-key": api_key}, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                ctx = data.get("inputTokenLimit")
                out = data.get("outputTokenLimit")
                if ctx:
                    return int(ctx), int(out) if out else None
        except Exception:
            pass

    # Strategy 2b: Anthropic Models API (returns max_input_tokens + max_tokens)
    if provider_name == "anthropic" or "anthropic.com" in base_url:
        try:
            import httpx
            r = httpx.get(f"{base_url}/models", headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }, timeout=timeout)
            if r.status_code == 200:
                for m in r.json().get("data", []):
                    if m.get("id") == model_id:
                        ctx = m.get("max_input_tokens")
                        out = m.get("max_tokens")
                        if ctx:
                            return int(ctx), int(out) if out else None
        except Exception:
            pass

    # Strategy 3: OpenAI-compatible /v1/models
    try:
        # max_retries=0 -- see the docstring: this probe never raises, so a
        # failure is a miss. Retrying turned one 5s timeout into 16.7s.
        fetch_client = (client.with_options(timeout=timeout, max_retries=0)
                        if hasattr(client, "with_options") else client)
        resp = fetch_client.models.list()
        norm = _normalize_model_id("", model_id)
        for m in resp.data:
            if _normalize_model_id("", str(getattr(m, "id", ""))) == norm:
                ctx = None
                for attr in ("context_length", "context_window", "max_model_len",
                             "max_context_length", "max_input_tokens"):
                    v = getattr(m, attr, None)
                    if v:
                        try:
                            ctx = int(v)
                        except (TypeError, ValueError):
                            pass
                    if ctx:
                        break
                # OpenRouter puts max_completion_tokens inside top_provider;
                # Groq (and others) publish it as a top-level field directly
                # -- e.g. a 512-token classifier model like Llama Prompt
                # Guard 2 reports max_completion_tokens=512 right on the
                # model object, distinct from context_window/context_length.
                out = None
                for attr in ("max_completion_tokens", "max_output_length",
                             "max_output_tokens"):
                    v = getattr(m, attr, None)
                    if v:
                        try:
                            out = int(v)
                        except (TypeError, ValueError):
                            continue
                        break
                if out is None:
                    tp = getattr(m, "top_provider", None)
                    if isinstance(tp, dict):
                        out = tp.get("max_completion_tokens")
                        if out:
                            try:
                                out = int(out)
                            except (TypeError, ValueError):
                                out = None
                if ctx:
                    return ctx, out
    except Exception:
        pass
    return None, None


# A model's modalities do not change, but the name pointing at them does: a
# locally re-pulled `qwen2.5vl:7b` can be a different build by Friday. A week
# keeps the probe effectively free without pinning a stale answer forever.
VISION_CACHE_SECONDS = 7 * 24 * 3600


def _ollama_vision(base_url, model_id, headers, timeout):
    """Ollama's /api/show `capabilities`, which lists "vision" verbatim.

    An absent or empty list is None, not False: builds predating the field
    report nothing, and "did not say" must stay retryable.
    """
    import httpx
    show_url = base_url.replace("/v1", "") + "/api/show"
    r = httpx.post(show_url, json={"model": model_id}, headers=headers, timeout=timeout)
    if r.status_code != 200:
        return None
    capabilities = r.json().get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        return None
    return "vision" in [str(item).lower() for item in capabilities]


def _listing_vision(base_url, model_id, headers, timeout):
    """`architecture.input_modalities` from an OpenAI-compatible /models list.

    Read over raw httpx rather than `client.models.list()` — unlike the
    context-window probe next door, the field wanted here is a nested dict on
    a non-standard key, which the SDK's typed model object does not surface.
    OpenRouter publishes it for all 436 of its models; most other endpoints
    publish nothing, which is a None (unknown), never a False.
    """
    import httpx
    r = httpx.get(f"{base_url}/models", headers=headers, timeout=timeout)
    if r.status_code != 200:
        return None
    for entry in r.json().get("data", []):
        if not isinstance(entry, dict) or str(entry.get("id", "")) != str(model_id):
            continue
        architecture = entry.get("architecture")
        if not isinstance(architecture, dict):
            return None
        modalities = architecture.get("input_modalities")
        if not isinstance(modalities, list) or not modalities:
            return None
        return "image" in [str(item).lower() for item in modalities]
    return None


def probe_model_vision(client, model_id, provider_name="", timeout=MODEL_LIST_TIMEOUT_SECONDS):
    """Best-effort: ask the endpoint whether this model can read images.

    Returns True, False, or None for "the endpoint did not say". Never raises.

    The three-valued answer is the whole contract. Only True and False are
    cached; a timeout, a 404 or a silent endpoint stays None and gets asked
    again next time, because caching a failure would mark a vision model blind
    for a week on the strength of one dropped packet.

    Strategies, in order, and only ones verified against a live endpoint:
    1. Ollama native /api/show — `capabilities` contains "vision".
    2. OpenAI-compatible /models — `architecture.input_modalities` contains
       "image". Authoritative on OpenRouter.

    Gemini is deliberately absent: its native endpoint publishes
    `supportedGenerationMethods`, not modalities, so there is no vision signal
    to read. Those models are covered by the bundled catalog instead.
    """
    base_url = str(getattr(client, "base_url", "")).rstrip("/")
    api_key = str(getattr(client, "api_key", ""))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    identity = hashlib.sha256(api_key.encode()).hexdigest()[:12] if api_key else "anonymous"
    cache_key = f"vision|{provider_name}|{base_url}|{identity}|{model_id}"

    now = time.time()
    disk = _load_disk_cache()
    cached = disk.get(cache_key)
    if (isinstance(cached, dict) and isinstance(cached.get("vision"), bool)
            and (now - cached.get("ts", 0)) < VISION_CACHE_SECONDS):
        return cached["vision"]

    answer = None
    if provider_name in ("ollama", "ollama-cloud") or "ollama" in base_url:
        try:
            answer = _ollama_vision(base_url, model_id, headers, timeout)
        except Exception:
            answer = None
    if answer is None:
        try:
            answer = _listing_vision(base_url, model_id, headers, timeout)
        except Exception:
            answer = None

    if answer is not None:
        disk[cache_key] = {"ts": now, "vision": answer}
        _save_disk_cache(disk)
    return answer
