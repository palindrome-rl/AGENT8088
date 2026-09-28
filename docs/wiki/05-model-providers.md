# Model Providers

[← Wiki index](README.md)

Agent8088 speaks the OpenAI chat-completions protocol, so anything
OpenAI-compatible works — local or hosted.

## The 12 built-in providers

Verified from `BUILTIN_PROVIDERS` in `src/agent8088/providers.py`:

| Provider | Base URL | Key env var |
|---|---|---|
| `ollama` | `http://localhost:11434/v1` | — (local; ships a placeholder key) |
| `ollama-cloud` | `https://ollama.com/v1` | `OLLAMA_API_KEY` |
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` |
| `cerebras` | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` |
| `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `mistral` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| `moonshot` | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `anthropic` | `https://api.anthropic.com/v1/` | `ANTHROPIC_API_KEY` |

Each profile also carries a **label** (shown in the `/model` and `--setup`
pickers) and a **default model** — `qwen14b-tooluse-v3` for `ollama`,
`anthropic/claude-sonnet-4` for `openrouter`, `claude-sonnet-4-6` for
`anthropic`, and so on. The built-in `base_url` is seeded even when only
`api_key` + `model` are set, so a built-in needs nothing else to load.

> The comment header at the top of the shipped `config.txt` still lists
> `copilot` and omits `anthropic`. `providers.py` is authoritative — the 12
> above are what actually exists.

**Native tool-calling** is on for every provider except `ollama`, which is set
`native_tools=False` because local Ollama models use the prompt-based tool
convention. Any provider can be flipped with `provider.<name>.native_tools=0`
or `=1`.

## Reaching Anthropic / Claude

Claude has a built-in `anthropic` profile now, so the direct route needs no
`litellm`:

```ini
default_provider=anthropic
provider.anthropic.model=claude-sonnet-4-6
provider.anthropic.api_key_env=ANTHROPIC_API_KEY
```

**Via OpenRouter** is still useful when you want one key covering many vendors
(its default model is `anthropic/claude-sonnet-4`):

```ini
default_provider=openrouter
provider.openrouter.model=anthropic/claude-sonnet-4
provider.openrouter.api_key_env=OPENROUTER_API_KEY
```

**Direct, via litellm mode** remains available for any provider litellm knows
but Agent8088 does not:

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
/model                           # table of every configured provider
/models                          # picker over the active provider
/models groq                     # ...or a named one
/model setup                     # add/update a provider profile
```

`/models` fetches the live list from the provider's `/v1/models` (disk-cached
for an hour). When that request fails it falls back to the provider's bundled
`FALLBACK_MODELS` entry rather than asking you to type a name; only a provider
with neither reaches the free-text prompt.

### The `auto` ladder

`/model auto`, `auto:fast` and `auto:smart` select a **strength ladder** built
from `auto_chain=provider:model,provider:model,...` — cheapest rung first.
Escalation is evidence-driven: a truncated response, repeated invalid tool
calls, or no progress on a turn climbs one rung, and the rung resets at the
start of each turn. `auto` and `auto:fast` start at the bottom; `auto:smart`
starts at the top. Build a chain from your keyed providers with `/model auto
setup`, which probes reachability and orders candidates weakest-first.

The ladder is deliberately **not** `fallback_models`: fallback answers "what to
try when this model is unreachable" (ordered by preference), the ladder answers
"what is stronger" (ordered by capability). With any explicit model selected,
`auto_chain` is never read.

## Fallback chains

```ini
fallback_models=groq:llama-3.3-70b-versatile,gemini:gemini-2.0-flash
```

Tried in order when the primary fails with a **retryable** error — HTTP 429,
5xx, or a timeout/connection error. Deterministic failures (401, 400) do not
trigger fallback, because retrying a bad key on a different provider just wastes
a call. Before moving on, the same provider is retried with exponential backoff
(`api_max_retries`, default 3; `0` means immediate failover).

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
| temperature | `/temp <float>` at runtime; `provider.<name>.temperature` in `config.txt` overrides it for one provider |
| max agent turns | `/maxturns <int>` |
| `context_window` | `config.txt` — global fallback when the active model has no better value |
| `max_completion_tokens` | `config.txt` — global output ceiling, sized for long chat answers |
| `frequency_penalty`, `presence_penalty` | `config.txt` (0 by default; sent only when non-zero) |
| `timeout_seconds` | `config.txt` (default 120) |

`provider.<name>.context_window` and `provider.<name>.max_completion_tokens`
override the globals for one provider, and are auto-applied on `/model switch`.
When neither is set, Agent8088 probes the endpoint for the model's own limits —
Ollama's `/api/show`, Gemini's native model endpoint, Anthropic's Models API, or
the non-standard fields some OpenAI-compatible `/v1/models` responses carry
(including vLLM's `max_model_len`) —
and falls back to the globals only when the probe finds nothing. The active
pair is re-read by every token-limit consumer, so `/doctor` and the context
meter agree with the wire.

## Tool-calling compatibility

Agent8088 accepts both native `tool_calls` and the fine-tuned model's
text-marker format, so it works with models whose function-calling is weak or
absent. If the model emits a tool call as text, it's parsed; if it invents a
tool that doesn't exist, the agent is told what went wrong and loops so it can
recover, bounded to avoid infinite retries.

This is why a small local model still works: correctness doesn't depend on the
provider implementing tool-calling perfectly.

With `native_tools` enabled the full tool schemas go on the wire; with it off
(`ollama` by default) the prompt carries the tool index and `describe_tool` can
load one schema on demand. `tool_selection=hybrid` narrows the native schema set
to the most relevant tools per request.

## Fusion — cross-provider panel + judge

`/fusion <query>` is a one-shot consultation command that runs a blind panel
comparison across multiple providers. It sends the same query to one model from
each provider that has a working API key, collects their answers, then a judge
model picks the best one. This is **not** part of the normal agent loop — no
tools are given to panel members, and the exchange does not become part of
conversation history.

**Panel selection is automatic.** The command walks your configured providers,
picks one model from each that has a valid API key (that provider's default
model unless overridden), and queries them in parallel. Each panel member sees
only a minimal system prompt instructing it to answer directly; no special
context, no tool availability. This keeps the comparison fair and the answers
short.

**Judging is blind.** The judge model (configurable; defaults to your current
session model) sees the candidate answers labeled anonymously as "Answer A",
"Answer B", and so on in randomized order. The judge is never told which model
produced which answer. This prevents bias toward recognizable names or the
judge's own output. The judge picks a winner and explains the choice in a short
verdict paragraph.

**Output includes the winning answer verbatim, the judge's verdict, a table of
which panel members succeeded, timed out, or errored, and a token/cost footer.**
If a panel member errors or times out it's dropped; one slow or bad provider
does not sink the entire run. If everything fails, you're told plainly. If only
one model answers, fusion skips the judge entirely since there's nothing to
compare. If the judge gives an unparseable response, fusion falls back to the
first surviving answer and says so explicitly rather than silently guessing.

**Fusion is expensive.** It makes N panel calls plus one judge call, roughly
4–5× the cost of a single-model query. There is no confirmation prompt because
fusion makes no destructive changes, but the tool prints which models it is
about to call before making any request, so cost is visible up front.

**Configuration** is controlled via `config.txt`. Reference `fusion_max_panel`,
`fusion_member_timeout_s`, `fusion_judge_provider`, and `fusion_judge_model`,
and the remaining keys for parallelism and token limits. See the
[Configuration](02-configuration.md) doc for the complete list and defaults.
