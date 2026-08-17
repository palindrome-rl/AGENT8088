# Agent8088 — Feature Roadmap from Business Plan

> Extracted from the Palindrome Business Plan (33 pages).
> This document covers **only Agent8088** — the open-source execution engine.
> RPM (memory), Hylomorph (control), Mobile, and Socratus are separate subsystems
> documented elsewhere.

---

## Agent8088's Role

Agent8088 is the **open-source execution engine** inside Palindrome Runtime.

> *"Agent8088 is the open agent-execution engine. It manages model interaction,
> tools, execution loops, task state, retries, error handling, structured
> outputs, model-specific formatting, context management, memory access and
> completion verification."*

> *"Agent8088 normalizes the incompatible tool-use conventions of different
> model families into one internal Palindrome protocol. Developers should be
> able to move an agent among Qwen, Gemma, Apple Foundation Models, local GGUF
> models, cloud APIs and future model families without rebuilding the tool
> layer."*

**Core promise:** Change the model without rewriting the agent.

---

## Current State (as of this writing)

| Feature | Status | Notes |
|---|---|---|
| One-command install (macOS/Linux/Windows) | ✅ Done | `install.sh` + `install.ps1` |
| CLI setup wizard (`--setup`) | ✅ Done | Prompts for working dir, model, API key, web search |
| CLI flags (`--help`, `--version`, `--full-auto`, `--mode`, `--uninstall`, `--update`, `--setup`) | ✅ Done | argparse-based; plan sessions start with `/plan` |
| Cross-platform shell execution | ✅ Done | Windows `cmd.exe` + Linux `bash` |
| Permission layer (readonly → edit escalation) | ✅ Done | Per-action y/n prompts, one-shot grants |
| Security layers (sensitive files, network gate, path zones) | ✅ Done | 3 layers, config-driven |
| Tool alias resolution + arg transforms | ✅ Done | 20+ aliases, 18 arg transforms |
| Rich CLI UI (streaming, ESC interrupt, slash commands) | ✅ Done | 14 slash commands |
| Working directory defaults to CWD (not package dir) | ✅ Done | `os.getcwd()` default |
| Packageable (hatchling, entry points, `uv tool install`) | ✅ Done | Builds clean wheel |
| Config cleaned (no secrets, localhost Ollama default) | ✅ Done | |
| Git history scrubbed of secrets | ✅ Done | `git filter-repo` |
| **Local-model adapter (Ollama)** | ❌ Missing | Single `OpenAI()` client, no adapter abstraction |
| **Cloud-model adapter** | ❌ Missing | Same single client |
| **Palindrome tool protocol** | ❌ Missing | Uses raw `✿FUNCTION✿`/`✿ARGS✿` text parsing |
| **Model-specific formatting** | ❌ Missing | All models must emit same format |
| **Five-minute starter tutorial** | ❌ Missing | No QUICKSTART.md |
| **3 example agents** | ❌ Missing | None published |
| **Completion verification** | ❌ Missing | No structured success check |
| **Execution receipts** | ⚠️ Partial | `/trace` exists but not structured receipts |
| **Context management** | ⚠️ Partial | `CONTEXT_WINDOW` + `% ctx` hint, no active management |
| **Retry logic / error recovery** | ⚠️ Partial | `seen` set prevents loops, no structured recovery |
| **Tool scaffolding** | ❌ Missing | No `agent8088 tool create` |
| **Execution viewer** | ⚠️ Partial | `/trace` shows JSON, not a proper viewer |
| **Evaluation runs** | ❌ Missing | No model comparison on agent tasks |
| **Model routing** | ❌ Missing | No Router model for cost/latency optimization |

---

## Required Features (by plan phase)

### Phase 1: Establish the company surface (C4)

| Deliverable | Priority | Effort | Status |
|---|---|---|---|
| Package Agent8088 for one-command installation | High | Done | ✅ |
| Release command-line setup wizard | High | Done | ✅ |
| Validate installation on macOS, Ubuntu, Windows | High | Windows ✅, Ubuntu untested | ⚠️ |
| Add default local-model adapter (Ollama) | **High** | Medium | ❌ |
| Add one cloud-model adapter | **High** | Medium | ❌ |
| Publish a five-minute starter tutorial | **High** | Small | ❌ |

### Phase 2: Release the first model and benchmark (C5)

| Deliverable | Priority | Effort | Status |
|---|---|---|---|
| Release Agent8088 v0.2 with first-class Palindrome Action support | Medium | Medium | ❌ |
| Publish 3 complete example agents | **Medium** | Small | ❌ |
| Publish installation instructions | Low | Done | ✅ |

### Phase 3: Make RPM a real product (C3)

| Deliverable | Priority | Effort | Status |
|---|---|---|---|
| Integrate RPM directly into Agent8088 | Medium | Large | ❌ |
| Expose memory read/write/revise/forget/consolidate as typed tools | Medium | Medium | ❌ |
| Add user approval for sensitive memory classes | Low | Small | ❌ |

### Phase 4: Integrate deterministic control (C2-C3)

| Deliverable | Priority | Effort | Status |
|---|---|---|---|
| Embed Hylomorph policies inside Agent8088 | Medium | Large | ❌ |
| Support allowed/prohibited tools, cost limits, iteration limits | Medium | Medium | ⚠️ Partial |
| Support approval before sensitive actions | Low | Done | ✅ |
| Execution receipts (tools called, permissions checked, models used) | **Medium** | Medium | ❌ |

### Phase 5: Prove model independence (C2-C3)

| Deliverable | Priority | Effort | Status |
|---|---|---|---|
| Complete the Gemma tool-call adapter | **High** | Medium | ❌ |
| Complete the cloud adapter | **High** | Medium | ❌ |
| Normalize both into the Palindrome tool protocol | **High** | Large | ❌ |
| Run one agent task suite across multiple models without changing tools | High | Medium | ❌ |

### Phase 6: Build Palindrome Router (C3)

| Deliverable | Priority | Effort | Status |
|---|---|---|---|
| Route by task type, latency, cost, privacy | Low | Large | ❌ |
| Automatic escalation: Router → local → cloud → back to local | Low | Large | ❌ |

### Phase 9: Launch developer platform alpha (C2-C3)

| Deliverable | Priority | Effort | Status |
|---|---|---|---|
| Release tool scaffolding (`agent8088 tool create <name>`) | Medium | Medium | ❌ |
| Release execution viewer (show every model response, tool call, policy decision) | Medium | Medium | ⚠️ Partial |
| Implement evaluation runs (compare models on agent tasks) | Medium | Medium | ❌ |

---

## The Three Biggest Gaps

### 1. Model Adapters + Palindrome Tool Protocol

**What the plan wants:** Each model family (Qwen, Gemma, Apple Foundation Models, cloud APIs, local GGUF) gets an adapter that translates between its native tool-call format and a normalized internal "Palindrome tool protocol." Developers switch models without touching tool code.

**Current state:** One `OpenAI()` client (`engine.py:218`). Works with any OpenAI-compatible endpoint (Gemini, Cerebras, Ollama), but can't handle models that DON'T speak OpenAI format (Apple Foundation Models, GGUF models with different conventions). All models must emit `✿FUNCTION✿: name ✿ARGS✿: {...}` — if a model emits a different format, agent8088 can't parse it.

**What to build:**
- `adapters.py` module with a base `ModelAdapter` class
- `OllamaAdapter` (local, default, no API key)
- `CloudAdapter` (any OpenAI-compatible endpoint, needs API key)
- Normalized internal tool-call format (the "Palindrome protocol")
- Each adapter translates its model's native format to/from the protocol

### 2. Five-Minute Starter Tutorial + Example Agents

**What the plan wants:** "A developer to create and run a tool-using agent in less than five minutes." Three complete example agents published.

**Current state:** No QUICKSTART.md (we wrote one earlier but it was part of the reverted packaging round). No example agents.

**What to build:**
- `QUICKSTART.md` — 5-minute walkthrough: install → configure → first query → first tool call → first multi-step agent
- `examples/` directory with 3 agents:
  - `examples/simple.py` — one tool call (calculate)
  - `examples/file_ops.py` — read + write files
  - `examples/multi_step.py` — plan + execute + verify

### 3. Completion Verification

**What the plan wants:** Agent8088 should verify whether a task "has actually succeeded" — not just run until the model stops emitting tool calls.

**Current state:** The agent loop (`run_agent`) runs until the model stops emitting tool calls or hits `max_turns`. There's no structured check that the task was completed correctly. The model could claim success without actually doing the work.

**What to build:**
- A `verify_completion()` function that checks tool results against the original task
- Structured "completion evidence" in the trace (what tools ran, what they returned, whether the output matches the request)
- Optional: a second model call to verify the first model's work

---

## Recommended Build Order

Matching the plan's phase sequence and prioritizing by impact:

| Step | Feature | Effort | Plan Phase |
|---|---|---|---|
| 1 | **Adapters module** (local Ollama + cloud) | Medium | Phase 1 C4 |
| 2 | **QUICKSTART.md** (5-minute tutorial) | Small | Phase 1 C4 |
| 3 | **3 example agents** | Small | Phase 2 C5 |
| 4 | **Palindrome tool protocol** (normalized format) | Large | Phase 5 |
| 5 | **Gemma adapter** | Medium | Phase 5 |
| 6 | **Execution receipts** (structured trace) | Medium | Phase 4 C3 |
| 7 | **Tool scaffolding** (`agent8088 tool create`) | Medium | Phase 9 C2 |
| 8 | **Execution viewer** (proper UI for traces) | Medium | Phase 9 C3 |
| 9 | **Evaluation runs** (model comparison) | Medium | Phase 9 C4 |
| 10 | **Completion verification** | Large | Section 3.2 |
| 11 | **Model routing** (Router model) | Large | Phase 6 |

---

## What Agent8088 Does NOT Need (per plan scope)

These are separate subsystems, not Agent8088 features:

- **RPM** (memory) — separate product, integrates INTO Agent8088 but is not built by Agent8088
- **Hylomorph** (deterministic control) — separate subsystem, embeds policies INTO Agent8088
- **Palindrome Mobile** (iPhone app) — separate application
- **Socratus** (notes/memory app) — separate application
- **Palindrome Models** (Action 14B, Edge, Router, Vision) — model training, not Agent8088
- **Enterprise controls** (identity, team memory, audit) — Phase 11, separate from Agent8088 core

Agent8088's job is execution: take a model, take tools, run the loop, handle errors, verify completion. Memory and control plug INTO it; they are not built BY it.
