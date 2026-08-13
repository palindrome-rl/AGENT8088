# Agent8088 — Scope vs Current State Gap Analysis

> Based on `docs/Agent8088_Scope.md` (the comprehensive scope document from Palindrome Research Labs).
> Cross-referenced against the actual codebase on the `development` branch.

---

## The 10 Harness Capabilities — Status

| # | Capability | Scope Target | Current State | Gap |
|---|---|---|---|---|
| **1** | **Permission modes** | Selectable modes: readonly, ask-per-action, auto-approve-safe, full-auto, plan-only; per-tool/per-class rules; approval prompts surfaced to user; denials as recoverable errors | ✅ Partial — readonly/edit modes, per-action y/n prompts, sensitive file blocklist, path zones, `_handle_escalation` popup, `check_permission()`, `grant_escalation()`, `seen.discard()` retry | Missing: plan-only mode, per-class rules (e.g. "network always asks"), Hylomorph integration |
| **2** | **MCP support** | Act as MCP host/client — discover/invoke external MCP servers; expose Agent8088 tools as MCP server | ❌ Not started | Complete gap — no MCP code exists |
| **3** | **Messaging-app surfaces** | Slack, Discord, Telegram, WhatsApp, email as interaction channels | ❌ Not in repo (WhatsApp/Slack on separate laptop) | Complete gap in this repo |
| **4** | **Scheduling** | Cron/interval runs, one-shot future runs, event/webhook triggers; durable schedule store; overlap/catch-up policy | ✅ Partial — `schedule_task` tool with cron add/list/remove, crontab integration | Missing: durable store, event/webhook triggers, overlap/catch-up, one-shot future runs |
| **5** | **Sub-agent spawning** | Spawn child agents with scoped tools/permissions/budgets; parallel+sequential fan-out; result aggregation; inherited policy; cross-device delegation | ✅ Partial — `spawn_subagent` tool, 4 profiles (general-purpose, explore, coder, researcher), depth-bounded, animated UI, `SUBAGENT_SPECS` | Missing: parallel fan-out, cross-device delegation (phone→server), budget inheritance, sequential chaining |
| **6** | **Memory & context-window management** | Token budgeting, compaction/summarization, relevance-based inclusion, spill to RPM, RPM as typed tools | ⚠️ Minimal — `CONTEXT_WINDOW` tracking, `% ctx` hint, `/compact` summarization, named sessions | Missing: token budgeting, relevance-based inclusion, RPM integration (memory tools), spill-to-RPM, recall-on-demand |
| **7** | **Error handling & retries** | Typed error taxonomy, retry policies with backoff, failure classification, halt-vs-continue in plans, conditional branching, recovery strategies in traces | ⚠️ Partial — `seen` set prevents loops, hallucinated-tool recovery feeds back real tools, backend errors wrapped, `_guard_answer`, `_redact_secrets`, `_preflight_refusal`, `strip_reasoning` | Missing: typed error taxonomy, retry with backoff, conditional branching in plans, halt-on-failure semantics, recovery strategies as trace data |
| **8** | **Streaming responses** | Token streaming + structured progress events (tool started/finished, plan step, approval requested) | ✅ Done — `on_token` callback, live streaming, `EscListener`, `_StatusLine`, `_SubStatusLine`, reasoning hidden by default, sanitized when shown | Missing: structured progress events (tool started/finished as machine-readable events, not just visual) |
| **9** | **Execution time limits** | Run-level wall-clock, per-step, per-plan limits, iteration caps, cost ceilings, graceful termination preserving state | ⚠️ Partial — per-tool `timeout` in tools.txt, `max_turns` on agent loop, Docker memory+CPU caps | Missing: run-level wall-clock, per-step/per-plan limits, cost ceilings, graceful termination with state preservation |
| **10** | **Sandboxed & dockerized execution** | Container-isolated tool execution, per-run ephemeral workspaces, proper Python sandbox replacing `eval()`, reproducible environments | ✅ Partial — `run_sandboxed` Docker tool (no network, memory-capped, auto-removed), `calculate` uses `eval()` with restricted builtins | Missing: per-run ephemeral workspaces, `eval()` replacement with proper sandbox, reproducible environments for trace generation |

---

## Wave Sequencing Status

| Wave | Capabilities | Status |
|---|---|---|
| **Wave 1** — close paper gaps & make traces trustworthy | #7 error handling, #9 execution limits, #10 sandboxing, #8 streaming | #8 done, #7/#9/#10 partial |
| **Wave 2** — safety & ecosystem | #1 permissions (with Hylomorph), #6 memory/context (with RPM), #2 MCP | #1 partial, #6 minimal, #2 not started |
| **Wave 3** — surfaces & scale | #3 messaging, #4 scheduling, #5 sub-agents (cross-device) | #4/#5 partial, #3 not started |

---

## What's Missing — Ranked by Scope Priority

### Wave 1 Gaps (highest priority — make traces trustworthy)

1. **Typed error taxonomy + retry policies** (Capability 7) — currently errors are caught with broad `except Exception` and returned as strings. Need: `ToolError`, `SchemaError`, `TimeoutError`, `PermissionDenied`, `ModelError` classes; retry with exponential backoff; halt-vs-continue in plans.

2. **Run-level execution limits** (Capability 9) — currently only per-tool timeout + max_turns. Need: wall-clock limit on the entire run, per-step limits in plans, cost ceiling, graceful termination that saves state for resumption.

3. **Plan conditional branching + error recovery** (Capability 7, paper gap) — `_exec_plan` runs steps sequentially but a failed step doesn't halt — cascading errors. Need: `if step fails → stop` semantics, conditional branching (`if X then Y else Z`), retry-on-failure within a step.

4. **Structured progress events** (Capability 8) — streaming works visually but there's no machine-readable event stream (tool_started, tool_finished, approval_requested, plan_step). Needed for UI/mobile integration and trace instrumentation.

### Wave 2 Gaps (safety & ecosystem)

5. **MCP host/client** (Capability 2) — zero code. Need: discover MCP servers, map their tools into `TOOL_SPECS`, invoke them through `run_tool()`. Also expose Agent8088 tools as an MCP server.

6. **RPM memory integration** (Capability 6) — only `/compact` exists. Need: `read_memory`, `write_memory`, `revise_memory`, `forget_memory`, `consolidate_memory` as typed tools; token budgeting; relevance-based context inclusion; spill to RPM + recall.

7. **Permission mode: plan-only** (Capability 1) — need a mode where the agent can only execute plans, not individual tool calls. Also per-class rules (network always asks, shell safe commands auto-approve).

### Wave 3 Gaps (surfaces & scale)

8. **Messaging channels** (Capability 3) — Slack/Discord/Telegram/WhatsApp. You mentioned WhatsApp+Slack on another laptop — need to merge in.

9. **Scheduling: durable store + event triggers** (Capability 4) — currently uses crontab. Need: durable schedule store (survives restarts), event/webhook triggers, one-shot future runs, overlap/catch-up policy.

10. **Sub-agent: parallel fan-out + cross-device** (Capability 5) — currently sequential single-child. Need: parallel `ThreadPoolExecutor` children, cross-device delegation (phone → server worker), budget/permission inheritance.

---

## What We Have That's Working Well (improvement opportunities)

| Feature | Current | Could improve |
|---|---|---|
| **Provider registry** | 11 built-in providers, `load_providers()` from config, `get_client()`, built-in base_urls | Add Palindrome tool protocol (normalized internal format) so non-OpenAI-compat models work |
| **Tools** | 20 tools, 10 modes (shell, read_text, write_text, python_eval, http_get, http_post, subagent, cron, docker, browser) | Add `http_post` with headers/body/filter to tools.txt format (currently config-only) |
| **Subagents** | 4 profiles, depth-bounded, animated UI | Add parallel fan-out, `ThreadPoolExecutor`, result aggregation |
| **Skills** | 5 installable packages | Add skill validation (no tool name collisions with core), skill marketplace listing |
| **Security** | SSRF guard, secret redaction, pre-flight refusal, hidden CoT, guard_answer | Add typed permission policies (Hylomorph integration), per-class rules |
| **Trace saving** | `/trace save` to Documents, per-turn recording | Add structured trace format (tool_selection, valid_calls, action_sequences, failed_paths, recovery_strategies) for the Palindrome Loop |
| **Sessions** | Named, persistent, `/resume`, `/compact` | Add token budgeting, relevance-based context inclusion, RPM spill |
| **Install** | One-line for macOS/Linux/Windows, `--setup` wizard | Add Ubuntu validation in CI, QUICKSTART.md, 3 example agents |

---

## Delivery Roadmap Status

| Phase | Scope says | Status |
|---|---|---|
| Phase 1 | One-command install, wizard, 3-platform validation, local+cloud adapter, 5-min tutorial, alpha, tool-protocol draft, 20 installs | ~80% done — install works, wizard works, 11 providers, but no QUICKSTART.md, no Ubuntu CI validation, no tool-protocol draft, no 3 example agents |
| Phase 2 | v0.2, first-class Palindrome Action support, 3 example agents, 50 installs | Not started — needs example agents + Palindrome Action model card |
| Phase 3 | RPM integration, memory tools | Not started |
| Phase 4 | Hylomorph policies, execution receipts | Not started |
| Phase 5 | Gemma + cloud adapters, portability proof, model routing | ~40% done — providers work, but no Palindrome tool protocol, no portability proof, no routing |
| Phase 6 | Router integration | Not started |
| Phase 7 | Cross-device delegation | Not started |
| Phase 9 | Developer SDK, tool scaffolding, execution viewer, evaluation runs | Not started |

---

## The 3 Biggest Gaps (matching scope priority)

1. **Typed error handling + retry policies** (Wave 1, Capability 7) — the paper itself flags this. Plans don't halt on failure. No retry with backoff. No typed errors. This gates trace quality.

2. **MCP support** (Wave 2, Capability 2) — zero code. The scope says Agent8088 should be an MCP host AND publish its own tools as an MCP server. This is a major ecosystem play.

3. **QUICKSTART.md + 3 example agents** (Phase 1-2) — the scope says "a developer creates and runs a tool-using agent in under five minutes." No tutorial exists. No example agents exist. This is the developer-acquisition gate.

---

## Open Decisions from the Scope (Section 10)

| Decision | Status | Notes |
|---|---|---|
| Permission-mode split (A8088 vs Hylomorph) | Unsettled | Which decisions are local UX vs delegated to policy engine |
| Sandbox strategy (container-per-run vs per-tool vs persistent) | Unsettled | Affects always-on/mobile deployment |
| Sub-agent model (in-process vs separate processes vs remote workers) | Unsettled | Affects budget/permission/trace composition |
| Scheduling ownership (inside A8088 vs external service) | Unsettled | Affects always-on/mobile deployment |
| Tool-protocol vs MCP boundary | Unsettled | How much of internal protocol is published as developer-facing |
| Benchmark replacement | Unsettled | Which external suites replace the self-authored 63-test benchmark |

---

## Summary Numbers

| Metric | Count |
|---|---|
| Capabilities total (scope) | 10 |
| Capabilities done or partial | 7 |
| Capabilities not started | 2 (MCP, messaging) |
| Capabilities minimal | 1 (memory/context) |
| Tools | 20 |
| Tool modes | 10 (shell, read_text, write_text, python_eval, http_get, http_post, subagent, cron, docker, browser) |
| Subagent profiles | 4 |
| Skill packages | 5 |
| Built-in providers | 11 |
| CLI slash commands | 25+ |
| Test files | 10 (94 pass, 12 fail on old-behavior tests) |
| Engine lines | 1,424 |
| CLI lines | 1,633 |
| Providers lines | 67 |
| Total package lines | ~3,124 |
| Dependencies | 3 (openai, rich, InquirerPy) |