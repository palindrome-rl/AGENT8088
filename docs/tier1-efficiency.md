# Tier 1 efficiency improvements

Permission defaults are unchanged.

## Tool recovery

The shared execution boundary retains the `Error:` prefix for compatibility and
adds JSON fields `code`, `recoverable`, and `suggested_action`. Known argument
errors can be corrected and retried. A timeout is not automatically retryable:
side effects may already have happened. Unknown failures remain conservative.
This is a common-boundary improvement, not a claim that every third-party tool
now returns structured errors. Tool-specific validation messages remain intact.

## Result reuse

Within one agent run, up to 16 successful `read_text` results (64,000 characters
each) can be reused. Keys include canonical arguments, resolved file identity,
size, modification/change timestamps and permission mode. Non-read operations
invalidate this cache. Writes, shell commands, browser actions and opaque CLIs
are never treated as reusable results. Existing usable-search deduplication is
retained. This is not an idempotency guarantee for external actions; retry safety
still needs operation-specific logic.

## Prompt prefixes and measurement

Tool schemas and textual tool descriptions have deterministic name ordering.
The existing stable-instructions-before-runtime-context boundary is retained.
Telemetry records a hash of the stable prefix and schemas (never their content),
first streamed token latency when observable, and cached input tokens when
reported by the provider. No provider cache hit or percentage saving is assumed.
Benchmark cold/warm requests with identical prefixes on the actual deployment.

## Cost reader

Set `model_telemetry=1` in the existing configuration and restart to record future
calls. `/cost` reads the local telemetry file; `/cost <task-id>` filters a task.
New calls record task IDs where the active turn budget is available. Older and
out-of-turn records remain explicitly unattributed.

Costs are configured-rate estimates, not invoices. Missing token usage, unknown
rates and fallback models without matching active-model pricing remain unknown.
Streamed usage is preserved when the compatible API supplies it. Summed call
latency is not wall time because calls can overlap. The reader processes at most
the last 16MB and reports partial history and malformed records.

## Tests

Run `python -m pytest tests/test_efficiency.py tests/test_cli_anything_integration.py
tests/test_fallback_retry.py tests/test_browser_routing_prompt.py
tests/test_web_server_error_contract.py tests/test_tool_call_arg_recovery.py -q`.
Live checks use the existing configured model in the actual Agent8088 REPL:
read a fixture, recover from a missing filename, and inspect `/cost`.

## Result reuse and loop breaking are two mechanisms, not one

Narrowing reuse to unchanged file reads is correct, but the guard it replaced
was also the loop breaker, and that one has to cover every tool. Serving a
repeat from the previous output leaves the round's `executed` flag False, which
is what trips the stall detector; re-running the tool sets it True, so a model
re-emitting one identical `execute_shell` ran it once per round until
`max_turns` — repeating the side effect each time. Both now exist side by side:
`read_results` for reuse, `last_completed` for the loop breaker.

A consequence worth knowing: because any non-read clears `read_results`, the
read cache is only reachable when two reads of the same file are separated by
*another read*. Consecutive repeats are caught by the loop breaker first.

## What was measured, and what was not

Verified end to end through the real REPL against a scripted OpenAI-compatible
endpoint, capturing every request body:

| Claim | Evidence |
|---|---|
| Structured errors reach the model | `Tool result (read_text):` carried `Error: …` plus `{"code":"tool_failure","recoverable":false,"suggested_action":…}` |
| Repeat loop is broken | identical `execute_shell` executed **once**; second call served from cache; run stopped after 3 rounds, not `max_turns` |
| Read cache serves an A→B→A re-read | third read returned from cache without touching disk |
| Prefix is stable | **1** distinct `stable_prefix_hash` across **16** model calls spanning 5 turns |
| `/cost` reads back real data | 16 calls grouped under 5 task IDs, estimate shown with the not-an-invoice caveat |

Not measured, and not claimed:

- **No provider-side cache hit was observed.** `cached_input_tokens` stayed
  `None` because the test endpoint does not report it. The stable prefix is a
  precondition for provider caching, not evidence that any provider cached.
  The 41–80% figures in the source material are other people's systems.
- **Deterministic tool ordering is a no-op today.** `TOOL_SPECS` is built from
  `tools.txt` in file order, which is already deterministic. Sorting only
  changes anything once MCP servers merge tools in at discovery time — which is
  exactly the case the ordering guard exists for, but it is untested here.
- **No real model was exercised.** The scripted endpoint proves the harness
  plumbing; it says nothing about whether a given model acts on
  `suggested_action`. That needs a run against a real provider.
