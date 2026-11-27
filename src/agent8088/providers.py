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

import csv, hashlib, json, os, re, stat, subprocess, sys, tempfile, time
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


# Why the last live listing per provider failed, so a caller that got the
# static fallback (or nothing) can say "offline list -- discovery failed: why"
# instead of passing the fallback off as what the server offers.
LAST_LIST_ERROR: dict = {}


def last_list_error(provider_name):
    """The exception the last failed list_models() call hit, or None."""
    return LAST_LIST_ERROR.get(provider_name)


def list_models(provider_name, client=None, timeout=MODEL_LIST_TIMEOUT_SECONDS, fallback=True):
    """Fetch available models from provider's /v1/models endpoint.
    Disk-cached for 1 hour. Falls back to FALLBACK_MODELS on error
    (the error is kept in LAST_LIST_ERROR)."""
    LAST_LIST_ERROR.pop(provider_name, None)
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
    except Exception as exc:
        LAST_LIST_ERROR[provider_name] = exc
        return list(FALLBACK_MODELS.get(provider_name, [])) if fallback else []


# What a local Ollama runs a model at when nothing sets num_ctx, on the
# smallest GPUs. Current Ollama picks 4k / 32k / 256k by VRAM (<24 / 24-48 /
# >=48 GiB), which this client cannot see -- so it is a floor, not a guess.
OLLAMA_DEFAULT_NUM_CTX = 4096


def is_local_ollama(provider_name="", base_url=""):
    """A self-hosted Ollama (not Ollama Cloud), where num_ctx is the server's."""
    if provider_name == "ollama-cloud" or "ollama.com" in (base_url or ""):
        return False
    return provider_name == "ollama" or ":11434" in (base_url or "")


# Loading a model to read its served context can take a while on a slow disk.
# The first request would wait for the same load, so it is not extra time.
OLLAMA_LOAD_TIMEOUT_SECONDS = 120


def _ollama_ps_context(root, model_id, headers, timeout):
    """The loaded model's context from /api/ps, or None when it isn't loaded."""
    import httpx
    bare = model_id[:-len(":latest")] if model_id.endswith(":latest") else model_id
    r = httpx.get(f"{root}/api/ps", headers=headers, timeout=timeout)
    if r.status_code != 200:
        return None
    for entry in r.json().get("models", []) or []:
        name = str(entry.get("name") or entry.get("model") or "")
        if name in (model_id, bare, f"{bare}:latest"):
            value = entry.get("context_length")
            if isinstance(value, int) and value > 0:
                return value
    return None


def ollama_served_context(base_url, model_id, api_key="", timeout=MODEL_LIST_TIMEOUT_SECONDS,
                          load=False):
    """The context Ollama will actually run `model_id` with, and where it came from.

    /api/show's "<arch>.context_length" is the model's *maximum*; Ollama loads
    it at num_ctx, which defaults to a few thousand tokens. Its
    OpenAI-compatible /v1/chat/completions has no way to raise that per
    request (documented: "The OpenAI API does not have a way of setting the
    context size"), and it truncates an over-long prompt silently instead of
    rejecting it. So the engine must size its window to the served value:
      1. the loaded model's context in /api/ps (newer Ollama reports it),
      2. a `num_ctx` PARAMETER baked into the model (/api/show parameters),
      3. with load=True, loading the model (/api/generate with no prompt,
         which only loads it) and reading /api/ps again,
      4. OLLAMA_CONTEXT_LENGTH in this environment (when Ollama runs here).
    Returns (tokens, source), or (None, "unknown") when none of those say --
    Ollama's own default depends on the server's VRAM. Never raises.

    Without step 3 a model that wasn't loaded yet had no served value, so the
    engine sized the window to the model's maximum and Ollama silently cut
    the prompt at its real, smaller context.
    """
    import httpx
    root = str(base_url or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        value = _ollama_ps_context(root, model_id, headers, timeout)
        if value:
            return value, "ollama ps"
    except Exception:  # noqa: BLE001 -- best-effort probe
        pass
    try:
        r = httpx.post(f"{root}/api/show", json={"model": model_id}, headers=headers, timeout=timeout)
        if r.status_code == 200:
            params = str(r.json().get("parameters") or "")
            match = re.search(r"^\s*num_ctx\s+(\d+)", params, re.MULTILINE)
            if match and int(match.group(1)) > 0:
                return int(match.group(1)), "model num_ctx"
    except Exception:  # noqa: BLE001
        pass
    if load:
        try:
            r = httpx.post(f"{root}/api/generate", json={"model": model_id}, headers=headers,
                           timeout=OLLAMA_LOAD_TIMEOUT_SECONDS)
            if r.status_code == 200:
                value = _ollama_ps_context(root, model_id, headers, timeout)
                if value:
                    return value, "ollama ps"
        except Exception:  # noqa: BLE001 -- a model that won't load answers below
            pass
    env = os.environ.get("OLLAMA_CONTEXT_LENGTH", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env), "OLLAMA_CONTEXT_LENGTH"
    return None, "unknown"


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


def _ollama_thinking(base_url, model_id, headers, timeout):
    """Ollama's /api/show `capabilities`: "thinking" for a model that reasons first. An absent or empty list is None."""
    import httpx
    show_url = base_url.replace("/v1", "") + "/api/show"
    r = httpx.post(show_url, json={"model": model_id}, headers=headers, timeout=timeout)
    if r.status_code != 200:
        return None
    capabilities = r.json().get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        return None
    return "thinking" in [str(item).lower() for item in capabilities]


def _listing_thinking(base_url, model_id, headers, timeout):
    """`supported_parameters` from an OpenAI-compatible /models list: "reasoning" or "include_reasoning" for a model
    that reasons. OpenRouter publishes it; an endpoint that lists no parameters says nothing (None)."""
    import httpx
    r = httpx.get(f"{base_url}/models", headers=headers, timeout=timeout)
    if r.status_code != 200:
        return None
    for entry in r.json().get("data", []):
        if not isinstance(entry, dict) or str(entry.get("id", "")) != str(model_id):
            continue
        parameters = entry.get("supported_parameters")
        if not isinstance(parameters, list) or not parameters:
            return None
        names = {str(item).lower() for item in parameters}
        return bool(names & {"reasoning", "include_reasoning", "reasoning_effort"})
    return None


def probe_model_thinking(client, model_id, provider_name="", timeout=MODEL_LIST_TIMEOUT_SECONDS):
    """Best-effort: does the endpoint say this model reasons before it answers? True, False or None. Never raises.

    The same rules as probe_model_vision: only True and False are cached (for VISION_CACHE_SECONDS), and a silent
    or failing endpoint stays None to be asked again. Ollama's /api/show first, then the /models listing."""
    base_url = str(getattr(client, "base_url", "")).rstrip("/")
    api_key = str(getattr(client, "api_key", ""))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    identity = hashlib.sha256(api_key.encode()).hexdigest()[:12] if api_key else "anonymous"
    cache_key = f"thinking|{provider_name}|{base_url}|{identity}|{model_id}"
    now = time.time()
    disk = _load_disk_cache()
    cached = disk.get(cache_key)
    if (isinstance(cached, dict) and isinstance(cached.get("thinking"), bool)
            and (now - cached.get("ts", 0)) < VISION_CACHE_SECONDS):
        return cached["thinking"]
    answer = None
    if provider_name in ("ollama", "ollama-cloud") or "ollama" in base_url:
        try:
            answer = _ollama_thinking(base_url, model_id, headers, timeout)
        except Exception:
            answer = None
    if answer is None:
        try:
            answer = _listing_thinking(base_url, model_id, headers, timeout)
        except Exception:
            answer = None
    if answer is not None:
        disk[cache_key] = {"ts": now, "thinking": answer}
        _save_disk_cache(disk)
    return answer


# --- live image probe ----------------------------------------------------------------------------------
#
# When neither the catalog nor the endpoint says whether a model reads images, ask it: send one small
# picture with a known answer and check the answer. Accepting the request proves nothing -- some servers
# drop image parts silently and the model then answers anyway -- so only a correct answer counts.

_PROBE_COLOURS = {"red": (220, 30, 30), "green": (30, 160, 60), "blue": (40, 80, 220),
                  "yellow": (240, 200, 20), "black": (20, 20, 20), "white": (245, 245, 245)}
_PROBE_QUESTION = ("This picture has two halves of different solid colours. Name the colour of the left "
                   "half, then the right half, in two words and nothing else.")
_IMAGE_REJECTED = re.compile(
    r"(?:does not|doesn't|do not|cannot|can't|not)\s+(?:support|accept|take|handle)\w*\s+(?:image|vision|multimodal)|"
    r"image(?:s|_url)?\s+(?:input\s+)?(?:is|are)\s+not\s+supported|not\s+a\s+(?:multimodal|vision)\s+model|"
    r"unsupported\s+(?:content|message)\s+(?:part\s+)?type[^.]*image|"
    r"(?:unknown|invalid|unexpected)\s+(?:content\s+)?(?:part\s+)?type[^.]*image", re.IGNORECASE)


# A model that cannot see often says so and then guesses; a guess that happens to name the right two
# colours must not count as seeing.
_CANNOT_SEE = re.compile(
    r"\b(?:can(?:no|')?t|cannot|unable to|not able to|don't|do not)\s+(?:see|view|read|process|access|open)\b[^.]*"
    r"\b(?:image|picture|attachment|photo)s?\b|\bno (?:image|picture) (?:was |is )?(?:provided|attached|shown)\b",
    re.IGNORECASE)


def image_input_rejected(error) -> bool:
    """Whether an error says the model takes no image input at all (not a per-request limit)."""
    return bool(_IMAGE_REJECTED.search(str(error)))


def _probe_picture(left: str, right: str) -> str:
    import base64
    import io
    from PIL import Image
    img = Image.new("RGB", (96, 48), _PROBE_COLOURS[left])
    img.paste(Image.new("RGB", (48, 48), _PROBE_COLOURS[right]), (48, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _probe_cache_key(client, model_id, provider_name) -> str:
    base_url = str(getattr(client, "base_url", "")).rstrip("/")
    api_key = str(getattr(client, "api_key", ""))
    identity = hashlib.sha256(api_key.encode()).hexdigest()[:12] if api_key else "anonymous"
    return f"vision-live|{provider_name}|{base_url}|{identity}|{model_id}"


def remember_image_input(client, model_id, provider_name, accepts: bool) -> None:
    """Record what was observed about a model's image input (a probe, or a rejected request)."""
    disk = _load_disk_cache()
    disk[_probe_cache_key(client, model_id, provider_name)] = {"ts": time.time(), "vision": bool(accepts)}
    _save_disk_cache(disk)


def cached_image_understanding(client, model_id, provider_name=""):
    """A probe answer already on disk for this endpoint, key and model, or None. Never calls anything."""
    if client is None or not model_id:
        return None
    cached = _load_disk_cache().get(_probe_cache_key(client, model_id, provider_name))
    if (isinstance(cached, dict) and isinstance(cached.get("vision"), bool)
            and time.time() - cached.get("ts", 0) < VISION_CACHE_SECONDS):
        return cached["vision"]
    return None


def probe_image_understanding(client, model_id, provider_name="", *, force=False, timeout=30):
    """Whether the model answers a question only a model that sees the picture can answer.

    True or False is cached on disk per endpoint, key and model for VISION_CACHE_SECONDS; force asks
    again. None (the endpoint could not be reached, or answered nothing) is not cached. Never raises.
    """
    import random
    if client is None or not model_id:
        return None
    key = _probe_cache_key(client, model_id, provider_name)
    if not force:
        cached = _load_disk_cache().get(key)
        if (isinstance(cached, dict) and isinstance(cached.get("vision"), bool)
                and time.time() - cached.get("ts", 0) < VISION_CACHE_SECONDS):
            return cached["vision"]
    left, right = random.sample(list(_PROBE_COLOURS), 2)
    messages = [{"role": "user", "content": [{"type": "text", "text": _PROBE_QUESTION},
                                             {"type": "image_url", "image_url": {"url": _probe_picture(left, right)}}]}]
    try:
        response = client.chat.completions.create(model=model_id, messages=messages, max_tokens=64,
                                                  temperature=0, timeout=timeout)
        message = response.choices[0].message
        text = " ".join(str(part or "") for part in (getattr(message, "content", ""),
                                                     getattr(message, "reasoning_content", ""))).lower()
    except Exception as exc:  # noqa: BLE001 -- a probe never breaks a turn
        if not image_input_rejected(exc):
            return None
        answer = False
    else:
        if not text.strip():
            return None
        if _CANNOT_SEE.search(text):
            answer = False
            remember_image_input(client, model_id, provider_name, answer)
            return answer
        named = [colour for colour in _PROBE_COLOURS if re.search(rf"\b{colour}\b", text)]
        answer = (len(named) >= 2 and left in named and right in named
                  and text.find(left) < text.rfind(right))
    remember_image_input(client, model_id, provider_name, answer)
    return answer
