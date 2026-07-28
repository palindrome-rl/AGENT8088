# Multi-Model Provider Support

Agent8088 supports 13 built-in model providers through a unified provider registry. Switch between providers without changing code — just update config or use the interactive picker.

---

## Built-in Providers

| # | Provider | base_url | API Key Env Var | Example Model |
|---|---|---|---|---|
| 1 | Ollama (local) | `http://localhost:11434/v1` | none (or `ollama`) | `qwen14b-tooluse-v3` |
| 2 | OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | `anthropic/claude-sonnet-4` |
| 3 | OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-4o` |
| 4 | Anthropic (Claude) | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| 5 | Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` | `gemini-2.0-flash` |
| 6 | Cerebras | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` | `gpt-oss-120b` |
| 7 | DeepSeek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| 8 | Groq | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| 9 | Mistral | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` | `mistral-small-latest` |
| 10 | Moonshot (Kimi) | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` | `kimi-k2.6` |
| 11 | Qwen (DashScope) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` | `qwen-plus` |
| 12 | Ollama Cloud | `https://ollama.com/v1` | `OLLAMA_API_KEY` | `gpt-oss:120b` |
| 13 | GitHub Copilot | `https://api.githubcopilot.com` | `GH_TOKEN` | `gpt-4o-mini` |

All providers use the OpenAI-compatible API format. Agent8088 uses the `openai` Python SDK with different `base_url` and `api_key` per provider — no dedicated adapters needed.

---

## Configuration

### Model ref format

The active model is specified in `provider:model_name` format:

```ini
# Active model
model=cerebras:gpt-oss-120b

# Fallback chain (tried on 429/503/connection errors):
fallback_models=groq:llama-3.3-70b-versatile,gemini:gemini-2.0-flash
```

### Provider API keys

Set API keys in `config.txt` using the `provider.<name>.api_key` key:

```ini
provider.cerebras.api_key=csk-...
provider.groq.api_key=gsk-...
provider.openrouter.api_key=sk-or-...
provider.gemini.api_key=AIza...
```

If no `provider.<name>.api_key` is set, agent8088 checks the environment variable listed in the table above (e.g. `CEREBRAS_API_KEY`, `GROQ_API_KEY`).

### Provider base URL overrides

Built-in base URLs work without config. Override if you need a custom endpoint:

```ini
provider.ollama.base_url=http://192.168.1.100:11434/v1
provider.openai.base_url=https://your-proxy.example.com/v1
```

### Custom providers

Add any OpenAI-compatible endpoint as a custom provider:

```ini
model=my-provider:my-model
provider.my-provider.base_url=https://your-endpoint.com/v1
provider.my-provider.api_key=your-key
```

---

## Interactive Model Picker

### `agent8088 --setup`

The setup wizard uses a fuzzy searchable picker (powered by InquirerPy):

1. **Working directory** — where the agent can read/write files
2. **Provider** — fuzzy search through all 13 providers, arrow keys to navigate, Enter to select
3. **API key** — for the selected provider
4. **Model** — fetches the provider's available models via `/v1/models`, shows them in a fuzzy picker
5. **Web search URL** — optional SearXNG endpoint

### `/models` in the REPL

List and switch models at runtime:

```
8088 > /models
Fetching models from cerebras...
? Select a model from cerebras:
>   gpt-oss-120b
    gemma-4-31b
    zai-glm-4.7
```

Type to search, arrow keys to navigate, Enter to select. The selected model is activated immediately.

To list models from a different provider:

```
8088 > /models groq
```

### `/model` quick switch

Switch by typing the full ref:

```
8088 > /model cerebras:gpt-oss-120b
8088 > /model groq:llama-3.3-70b-versatile
8088 > /model ollama:qwen14b-tooluse-v3
```

---

## Model Caching

Model lists are cached to avoid repeated API calls:

- **Cache file:** `~/.agent8088/models_cache.json`
- **TTL:** 1 hour
- **Two-tier:** in-memory (per-session) + on-disk (survives restarts)
- **Fallback:** if `/v1/models` is unavailable, uses a hardcoded list per provider

The cache is a JSON file keyed by provider name:

```json
{
  "cerebras": {
    "ts": 1785238298.23,
    "models": ["gemma-4-31b", "gpt-oss-120b", "zai-glm-4.7"]
  },
  "groq": {
    "ts": 1785238300.15,
    "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
  }
}
```

First call to `/models` fetches from the provider and writes the cache. Subsequent calls within 1 hour read from disk instantly — no API call.

---

## Fallback Chains

If the active model fails (429 rate limit, 503 service error, connection timeout), agent8088 tries each model in the `fallback_models` chain:

```ini
model=cerebras:gpt-oss-120b
fallback_models=groq:llama-3.3-70b-versatile,gemini:gemini-2.0-flash
```

If Cerebras returns 429, agent8088 switches to Groq. If Groq also fails, it tries Gemini. The fallback is per-turn — next turn restarts from the primary model.

---

## Architecture

### `src/agent8088/providers.py`

The provider registry module:

- `BUILTIN_PROVIDERS` — dict of 13 providers with base_url + api_key_env
- `PROVIDERS` — runtime registry, loaded from config + built-in defaults
- `load_providers(config)` — scans config.txt for `provider.<name>.*` keys
- `get_client_for(model_ref, timeout)` — returns `(OpenAI client, model_name)` for a `provider:model` ref
- `list_models(provider_name, client)` — fetches `/v1/models`, caches to disk, falls back to hardcoded list
- `get_fallback_chain(config)` — parses `fallback_models` config
- `_normalize_model_id(provider_name, model_id)` — strips `models/` prefix for Gemini only

### `src/agent8088/engine.py`

- `get_client()` calls `providers.get_client_for(MODEL_REF)`
- `create_completion()` accepts a `model` parameter for fallback switching
- `run_agent()` catches 429/503/connection errors and tries the fallback chain

### `src/agent8088/cli.py`

- `--setup` wizard uses InquirerPy fuzzy picker for provider + model selection
- `/models` command fetches + displays + switches models
- `/model` command quick-switches by full ref
- Banner shows provider name + base_url

---

## Comparison with Hermes and OpenClaw

| Feature | Agent8088 | Hermes | OpenClaw |
|---|---|---|---|
| Providers | 13 built-in | 33+ (plugin system) | 40+ (plugin system) |
| Adapter type | Single OpenAI SDK + provider registry | ProviderProfile registry + dedicated adapters | registerProvider() plugin system |
| Model picker | InquirerPy fuzzy search | `hermes model` wizard | `openclaw onboard` wizard |
| In-session switch | `/model` + `/models` | `/model` | `/model` |
| Model caching | JSON file, 1hr TTL | Two-tier (memory + disk), 1hr | Static plugin catalogs |
| Fallback chains | Yes (config-driven) | Yes (config-driven) | Yes (config-driven) |
| Custom providers | `provider.<name>.base_url` in config | Custom endpoint in `hermes model` | `models.providers.<id>` in config |
| LiteLLM | No (own registry) | No (own registry) | No (own registry) |

All three projects use their own provider registry — none use LiteLLM. The pattern is: OpenAI SDK as the HTTP substrate + a registry of provider profiles + config-driven switching.

---

## Adding More Providers

To add a new provider:

1. Add an entry to `BUILTIN_PROVIDERS` in `providers.py`:
   ```python
   "together": {"base_url": "https://api.together.xyz/v1", "api_key_env": "TOGETHER_API_KEY"},
   ```

2. Add fallback models:
   ```python
   "together": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
   ```

3. Rebuild + reinstall. The new provider appears in the picker automatically.

For providers that need custom logic (non-OpenAI API format, OAuth flows, special headers), create a dedicated adapter file — following the pattern Hermes uses for Anthropic, Bedrock, and Gemini native APIs.