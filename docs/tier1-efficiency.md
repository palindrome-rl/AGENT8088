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
