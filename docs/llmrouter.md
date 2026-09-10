# Optional LLMRouter selection

This integration selects an initial model for `auto` / `auto:fast`. It does not
execute tools, forward model requests, replace permissions, or replace the existing
quality escalation and provider fallback. Explicit models and `auto:smart` bypass it.
Default is off. It is not a claim of measured routing-quality or cost improvements.

## Upstream review

Reviewed upstream commit `d1490a37202b1bea799ab3a601cab349d2291b08` (0.4.0):

- [README: training, data and route-only inference](https://github.com/ulab-uiuc/LLMRouter/blob/d1490a37202b1bea799ab3a601cab349d2291b08/README.md)
- [KNN documentation](https://github.com/ulab-uiuc/LLMRouter/blob/d1490a37202b1bea799ab3a601cab349d2291b08/llmrouter/models/knnrouter/README.md)
- KNN `route_single`, MetaRouter, DataLoader, Longformer embedding implementation,
  smallest/largest baselines, packaging, and the serving implementation.

The upstream batch methods also call LLMs; we do not use them. The older serving
adapter can fall back to random routing; we do not use that adapter either.
Our service calls `route_single` only and rejects recommendations outside the request's
eligible model list. It supports `knnrouter`, `smallest_llm`, and `largest_llm`.
Baseline smallest/largest are not learned task-complexity routers.

KNN documentation says no training, but this means no iterative neural optimization:
it still needs labeled historical scores, embeddings and a fitted classifier.
The implementation also loads training data during initialization, even for inference.
Supply training paths as well as the inference checkpoint, using absolute paths.
Longformer loads on first inference, so warm the service before enabling routing.

## Install separately

Do not install the upstream ML dependencies into Agent8088's environment.
Create a separate Python environment and install the reviewed upstream commit:

```powershell
python -m venv .llmrouter-venv
.\.llmrouter-venv\Scripts\python.exe -m pip install "git+https://github.com/ulab-uiuc/LLMRouter.git@d1490a37202b1bea799ab3a601cab349d2291b08"
```

Use trusted YAML/checkpoints only: upstream pickle/PyTorch artifacts may execute code.
Pin the resolved dependencies for your deployment after platform validation.
The normal Agent8088 installer does not download this environment or any weights.

Set `AGENT8088_ROUTER_TOKEN` to the same random local service token in the service
and Agent8088 environments. This is NOT the Ollama/provider API key. Do not put
credentials in YAML or source. Start the service from the Agent8088 checkout:

```powershell
.\.llmrouter-venv\Scripts\python.exe scripts/llmrouter_service.py --router knnrouter --config C:/routing/knn.yaml
```

For a baseline smoke test, use `--router smallest_llm` with YAML containing
`data_path.llm_data` pointing to a JSON model catalog. Catalog keys must exactly
match `provider:model` in your `auto_chain`; metadata must include a valid `size`
ending in B. Do not misrepresent placeholder test sizes as measured model metadata.

Then add to Agent8088's existing configuration and restart:

```ini
llmrouter_mode=shadow
llmrouter_url=http://127.0.0.1:8191/route
llmrouter_token_env=AGENT8088_ROUTER_TOKEN
llmrouter_timeout_seconds=2
```

Select `/model auto`. Configure `auto_chain` with models you have verified for
your workload and provider access. Shadow mode records but does not apply a route;
`enabled` applies it once at turn start. `off` restores the existing ladder.
No changes to default model/provider or the user's saved keys are made automatically.

## Safeguards and limitations

- Loopback-only HTTP, authentication, no redirects or proxy environment, bounded
  request/response size and network timeouts. Only the latest genuine user text,
  redacted through Agent8088, is submitted. No system prompt, tool results, or
  whole history. Redaction is not a guarantee against arbitrary personal data:
  enabling this explicitly trusts the local router with that user text.
- Non-text and oversized requests bypass selection. Multimodal learned routing is
  not implemented. Text references to documents do not convey the document itself.
- Candidates come from the operator's chain and are filtered for cooldown and
  context capacity including completion reserve. Capability, price and residency
  metadata are not inferred: curate the chain accordingly. Current budget controls
  remain in force; no new per-model cost optimizer is claimed.
- Unavailable service, rejected selection, missing token or malformed output keeps
  the existing ladder. Shadow mode never changes the selected model.
- Trace/log `routing_decision` reports selected ID, application status, latency and
  a bounded error category; never query text, credentials or response bodies.
- Inference is serialized; a busy worker returns 503. A hung worker must be restarted,
  but Agent8088 times out and continues. Health indicates initialization, not completed
  model warm-up. Run a route smoke test before production use.
- No live training, automatic dataset uploads, Gradio, or replacement of Web UI.

## Evaluation

Run adapter/service/engine tests and existing routing/subagent tests. Then exercise
the real Agent8088 with a real provider: tool use, missing-file recovery, shadow vs
enabled, service outage, explicit-model bypass and multiple user turns. Compare
selected models and traces, not just answer text. Test router usefulness separately
with at least two actual eligible models and held-out representative tasks; include
router latency, provider tokens, retries and final task correctness. One-candidate
baseline smoke tests prove plumbing, not learned routing quality or savings.
