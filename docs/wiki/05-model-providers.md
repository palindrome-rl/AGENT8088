# Model Providers

[← Wiki index](README.md)

Agent8088 speaks the OpenAI chat-completions protocol, so anything
OpenAI-compatible works — local or hosted.

## The 12 built-in providers

Verified from `BUILTIN_PROVIDERS` in `src/agent8088/providers.py`:

| Provider | Base URL | Key env var |
|---|---|---|
| `ollama` | `http://localhost:11434/v1` | — (local) |
| `ollama-cloud` | `https://ollama.com/v1` | `OLLAMA_API_KEY` |
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/...` | `GEMINI_API_KEY` |
| `cerebras` | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` |
| `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `mistral` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| `moonshot` | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/...` | `QWEN_API_KEY` |
| `copilot` | `https://api.githubcopilot.com` | `GH_TOKEN` |

> **Correction to the README.** It advertises "13 model providers" and lists
> **Anthropic** among them. There is no `anthropic` built-in. Claude is still
> reachable — two supported ways, below — but not as a built-in profile.

## Reaching Anthropic / Claude

**Via OpenRouter** (simplest — it's already built in, and its default model is
`anthropic/claude-sonnet-4`):

```ini
default_provider=openrouter
provider.openrouter.model=anthropic/claude-sonnet-4
provider.openrouter.api_key_env=OPENROUTER_API_KEY
```

**Direct, via litellm mode:**

```ini
default_provider=claude
provider.claude.api_mode=litellm
provider.claude.model=anthropic/claude-sonnet-4-5-20250929
provider.claude.api_key_env=ANTHROPIC_API_KEY
```

`api_mode=litellm` is the one case where `base_url` may be omitted — litellm
resolves the endpoint from the model id. Requires `litellm` installed.

## Custom OpenAI-compatible endpoints

Any local server (vLLM, LM Studio, llama.cpp, a self-hosted gateway):

```ini
default_provider=my-local-ai
provider.my-local-ai.base_url=https://llm.example.test/v1
provider.my-local-ai.model=custom-model
provider.my-local-ai.api_key_env=MY_LOCAL_AI_API_KEY
```

`--setup` offers **Custom OpenAI-compatible** in the picker and does this for
you. A URL ending in `/chat/completions` is normalised down to the `/v1` base
automatically.

A provider needs a `base_url` **and** a `model` to load; an incomplete profile
is silently dropped rather than half-registered (the exception being
`api_mode=litellm`, which needs no base URL).

## Switching models

```sh
agent8088 --model-setup          # wizard
```

At runtime:

```
/model cerebras:gpt-oss-120b     # switch provider + model
/models                          # fuzzy picker over the active provider
/models groq                     # ...or a named one
/model setup                     # add/update a provider profile
```

`/models` fetches the live list from the provider's `/v1/models`. If that call
fails, it falls back to asking you to type the model name rather than showing a
stale hardcoded list.

## Fallback chains

```ini
fallback_models=groq:llama-3.3-70b-versatile,gemini:gemini-2.0-flash
```

Tried in order when the primary fails with a **retryable** error — HTTP 429,
503, or a connection error. Deterministic failures (401, 400) do not trigger
fallback, because retrying a bad key on a different provider just wastes a call.

## API keys

Keys belong in `~/.agent8088/.env` (mode `0600`), pointed at by
`provider.<name>.api_key_env`. Resolution order, most explicit first:

1. the `.env` key store
2. an explicit `api_key` in `config.txt`
3. `os.environ`

`os.environ` is last deliberately: a stray `OPENAI_API_KEY` exported in your
shell for another tool must not silently redirect a configured provider.

Full details, including the automatic one-time migration out of `config.txt`,
are in [Configuration](02-configuration.md#api-keys-and-the-env-store).

## Sampling and context

| Setting | Where |
|---|---|
| temperature | `/temp <float>` at runtime |
| max agent turns | `/maxturns <int>` |
| `context_window` | `config.txt` — history trim budget |
| `frequency_penalty`, `presence_penalty` | `config.txt` |
| `timeout_seconds` | `config.txt` (default 120) |

## Tool-calling compatibility

Agent8088 accepts both native `tool_calls` and the fine-tuned model's
text-marker format, so it works with models whose function-calling is weak or
absent. If the model emits a tool call as text, it's parsed; if it invents a
tool that doesn't exist, the agent is told what went wrong and loops so it can
recover, bounded to avoid infinite retries.

This is why a small local model still works: correctness doesn't depend on the
provider implementing tool-calling perfectly.
