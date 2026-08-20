# Fix: length-cutoff retry — right nudge AND a bigger retry budget Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** When a model call is cut off at `MAX_COMPLETION_TOKENS` with zero visible answer (the whole budget was burned on hidden chain-of-thought), the automatic retry must (1) tell the model to stop reasoning and answer immediately, and (2) get a genuinely larger token budget — so a model that partially ignores the instruction still has room left to produce a real answer instead of hitting the identical wall again.

**Architecture:** Two small, independent changes to the same code path in `run_agent()`:
1. Thread an optional `max_tokens` override through `_create_completion_with_fallback()` so a specific turn can request more than the default budget.
2. In the `finish_reason == "length"` handler, pick the retry *message* based on whether `content` was empty, and have the *next* call use a doubled (context-window-capped) `max_tokens` whenever it's a length-retry turn.

Chosen over "nudge only" (cheap but no guarantee the model complies) and "raise the config default" (only reduces frequency, doesn't fix the retry itself) — this combines a behavioral nudge with a real, backend-agnostic capacity increase for the one attempt where it's needed, at low risk (still bounded by `CONTEXT_WINDOW`, still only one retry).

**Tech Stack:** Python, `pytest`, `agent8088.engine` module.

---

## Current context / bug report

Screenshot evidence (Agent8088 TUI): after a prior turn ("2×table" render, 10.9s/1220 tokens), the user typed `its still off`. The response cycle showed:

```
Model output reached its 8192-token limit. The pa[rtial response was not executed. Retry with one]
concise tool call; split large work across calls if nee[ded]
thinking (123s · ↑14127 tokens · esc to interrupt)
```

i.e. the internal token-limit warning leaked into the transcript as if it were the model's reply, and the agent was still churning on a second attempt 123s later.

### Root cause (confirmed by reading the code, not guessed)

- `src/agent8088/engine.py:1642-1694` (`_create_completion_with_fallback` → `create_completion`) passes a single `max_tokens=MAX_COMPLETION_TOKENS` (default **8192**, `engine.py:319-321`) that bounds the model's *entire* completion — both hidden chain-of-thought (streamed via the separate `delta.reasoning_content` field at `engine.py:1603-1605`/`1569-1571`, or inline `<think>` tags in `content`) **and** the visible answer/tool call.
- Local/small reasoning models (default `MODEL_NAME = qwen14b-tooluse-v3` via Ollama, `engine.py:316`) are known to reason at length. When reasoning alone exceeds 8192 tokens, the API returns `finish_reason == "length"` with **no visible content at all** — reasoning ate the whole budget before the model ever got to an answer or tool call. (Confirmed both mechanisms produce empty `content`: native `reasoning_content` deltas are never appended to `collected`, `engine.py:1603-1608`; inline, `_strip_reasoning` drops an unclosed `<think>` block entirely, `engine.py:5314-5323`.)
- `run_agent()`'s length-cutoff handler (`engine.py:6636-6654`) does not distinguish this case from a genuinely large in-progress answer, and always retries with the exact same `MAX_COMPLETION_TOKENS` budget and the message:
  > "Retry with one complete, concise tool call; split large work across calls if needed."
  Both are wrong for a reasoning-only overflow: the advice is irrelevant (the model didn't emit a large answer, it never got past thinking), and the unchanged budget means even a model that mostly complies with a better instruction may still run out of room. This is exactly the pattern in the screenshot: a *second* attempt still deep in a 123s/14127-token "thinking" phase after the first one already warned.
- Only **one** retry is allowed (`length_retries < 1`, `engine.py:6569,6647`). If the retry also hits the cap, `answer = _guard_answer(warning); return answer` (`engine.py:6651-6654`) makes the raw internal warning string the literal chat answer shown to the user — exactly what's visible in the screenshot in place of a real response to "its still off".
- This is a distinct code path from the existing "reasoning-only / empty turn" nudge at `engine.py:6703-6714` — that nudge only fires when `finish_reason == "stop"` with empty content; the `length` branch returns/continues *before* ever reaching it.
- Verified this logic is identical, byte-for-byte, on `development` (fast-forwarded to `origin/development` @ `38afe6e` as part of this investigation) — not a regression specific to the current branch.

### The fix (two parts, delivered together)

**Part A — message.** Reuse `content` (already computed by `_strip_reasoning` a few lines earlier) to pick the retry instruction: empty → "stop reasoning, answer now"; non-empty → keep today's "split large work" wording.

**Part B — budget.** Thread an optional `max_tokens` parameter through `_create_completion_with_fallback()`. In `run_agent()`, compute `turn_max_tokens` before each model call: `MAX_COMPLETION_TOKENS` normally, or `min(MAX_COMPLETION_TOKENS * 2, CONTEXT_WINDOW)` when `length_retries` is already `> 0` (i.e. this call is the one automatic retry after a cutoff). Pass it through to the call, and use it (not the fixed constant) in the warning text so the message stays accurate.

## Files likely to change

- `src/agent8088/engine.py:1642-1694` (`_create_completion_with_fallback`) — accept `max_tokens=None`, default to `MAX_COMPLETION_TOKENS`, pass through to both the primary and fallback `create_completion()` calls.
- `src/agent8088/engine.py:6605-6654` (`run_agent`, the model-call + length-cutoff handling) — compute `turn_max_tokens` per turn, pass it into the call, branch the retry message on `content`, use `turn_max_tokens` in the warning text.
- `tests/conftest.py` — **create**. Missing on this branch (present upstream in other worktrees, e.g. `.claude/worktrees/agent8088-readonly-docs-4b3520/tests/conftest.py`); needed so the new test can import/reload `agent8088.engine` without touching the real `~/.agent8088/config.txt`.
- `tests/test_length_retry.py` — **create**. Regression tests for the message branch and the budget-doubling.

## Step-by-step plan

### Task 1: Add the `engine` test fixture

**Objective:** Let tests import a fresh `agent8088.engine` module pointed at a nonexistent config file, so they never read/write the real user config.

**Files:**
- Create: `tests/conftest.py`

**Step 1: Write the fixture**

```python
"""Shared fixtures: load the agent8088 engine as a module."""
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_engine():
    os.environ["AGENT8088_CONFIG"] = str(ROOT / "_no_such_config.txt")
    os.environ["AGENT8088_SANDBOX"] = "local"
    sys.path.insert(0, str(ROOT / "src"))
    from agent8088 import engine as mod
    return importlib.reload(mod)


@pytest.fixture
def engine():
    """Fresh engine module per test (module globals are mutable in tests)."""
    return _load_engine()
```

**Step 2: Sanity-check it loads**

Run: `python3 -c "import sys; sys.path.insert(0, 'tests'); import conftest; e = conftest._load_engine(); print(e.MAX_COMPLETION_TOKENS, e.CONTEXT_WINDOW)"`
Expected: prints `8192 32768` (the defaults), no exception, no writes under `~/.agent8088/`.

### Task 2: Write the failing (RED) regression tests

**Objective:** Prove today's code (a) always sends the generic "split large work" nudge even on a reasoning-only overflow, and (b) always retries with the same, unchanged budget.

**Files:**
- Create: `tests/test_length_retry.py`

**Step 1: Write the tests**

```python
"""run_agent's retry after a MAX_COMPLETION_TOKENS cutoff must match the
failure mode: a reasoning-only overflow needs a 'stop thinking' nudge and a
bigger budget on retry — not the 'split large work across calls' advice and
the same unchanged budget, which just reproduces the same failure.
"""


def _length_response(content):
    return type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": content})(),
        "finish_reason": "length",
    })()]})()


def test_reasoning_only_overflow_gets_stop_thinking_nudge(monkeypatch, engine):
    """finish_reason=length with NO visible content (all budget spent on an
    unclosed <think> block) must retry with an instruction to stop reasoning
    and answer immediately — not the generic 'split work into calls' text."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        if len(calls) == 1:
            # Runaway, never-closed <think> block: _strip_reasoning drops it
            # entirely, so `content` at the cutoff is empty.
            return _length_response("<think>reasoning forever with no end")
        return _length_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    result = engine.run_agent([{"role": "user", "content": "its still off"}],
                              max_turns=5)

    assert len(calls) == 2, "expected exactly one automatic retry"
    retry_nudge = calls[1]["messages"][-1]["content"]
    assert "stop reasoning" in retry_nudge.lower() or "do not think" in retry_nudge.lower()
    assert "split large work" not in retry_nudge.lower()
    assert result == "the actual answer"


def test_large_answer_overflow_keeps_split_work_nudge(monkeypatch, engine):
    """finish_reason=length with SOME visible content (a real answer/tool call
    in progress) should keep today's 'split large work across calls' advice."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append({"messages": list(messages), "max_tokens": max_tokens})
        if len(calls) == 1:
            return _length_response("x" * 500)  # a real, oversized answer
        return _length_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=5)

    retry_nudge = calls[1]["messages"][-1]["content"]
    assert "split large work" in retry_nudge.lower()


def test_retry_after_length_cutoff_gets_a_bigger_budget(monkeypatch, engine):
    """The one automatic retry must ask for more than the default budget, so
    a model that keeps reasoning almost as long still has room for an answer."""
    calls = []

    def _fake(messages, tools, max_tokens=None, **kw):
        calls.append(max_tokens)
        if len(calls) == 1:
            return _length_response("<think>reasoning forever with no end")
        return _length_response("the actual answer")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake)
    engine.run_agent([{"role": "user", "content": "its still off"}], max_turns=5)

    assert calls[0] == engine.MAX_COMPLETION_TOKENS
    expected_retry_budget = min(engine.MAX_COMPLETION_TOKENS * 2, engine.CONTEXT_WINDOW)
    assert calls[1] == expected_retry_budget
    assert calls[1] > calls[0]
```

**Step 2: Run it to confirm RED**

Run: `python3 -m pytest tests/test_length_retry.py -v`
Expected: `test_reasoning_only_overflow_gets_stop_thinking_nudge` and `test_retry_after_length_cutoff_gets_a_bigger_budget` **FAIL** (today's code sends one fixed message and never varies `max_tokens`). `test_large_answer_overflow_keeps_split_work_nudge` already **PASSES** (documents existing behavior to guard against regressing it).

### Task 3: Thread `max_tokens` through `_create_completion_with_fallback`

**Objective:** Let a caller request a specific budget for one call instead of always using the module-level default.

**Files:**
- Modify: `src/agent8088/engine.py:1642-1694`

**Step 1: Update the signature and both call sites**

Current (relevant lines):

```python
def _create_completion_with_fallback(messages, tools, *, temperature, system_prompt,
                                     on_token, interrupt_check, trace, turn):
    emitted = False
    ...
        return create_completion(
            client, messages, tools, temperature=temperature,
            max_tokens=MAX_COMPLETION_TOKENS,
            ...
        )
    ...
            return create_completion(
                fallback_client, messages, tools, temperature=temperature,
                max_tokens=MAX_COMPLETION_TOKENS,
                ...
            )
```

New:

```python
def _create_completion_with_fallback(messages, tools, *, temperature, system_prompt,
                                     on_token, interrupt_check, trace, turn,
                                     max_tokens=None):
    emitted = False
    max_tokens = max_tokens if max_tokens is not None else MAX_COMPLETION_TOKENS
    ...
        return create_completion(
            client, messages, tools, temperature=temperature,
            max_tokens=max_tokens,
            ...
        )
    ...
            return create_completion(
                fallback_client, messages, tools, temperature=temperature,
                max_tokens=max_tokens,
                ...
            )
```

(Only the two `max_tokens=MAX_COMPLETION_TOKENS` lines change, to `max_tokens=max_tokens`; everything else in the function is untouched.)

### Task 4: Compute a bigger retry budget and branch the nudge in `run_agent`

**Objective:** Use the new parameter — double the budget on the one length-retry turn, and pick the right nudge message.

**Files:**
- Modify: `src/agent8088/engine.py:6605-6654`

**Step 1: Compute `turn_max_tokens` before the call**

Current:

```python
        try:
            with spin("thinking..."):
                response = _create_completion_with_fallback(
                    messages, round_tools_def, temperature=temperature,
                    system_prompt=round_system_prompt, on_token=on_token,
                    interrupt_check=interrupt_check, trace=trace, turn=turn,
                )
```

New:

```python
        # After a length cutoff, the one retry gets a bigger budget (capped by
        # the model's context window) — the same fixed budget would just
        # reproduce the same cutoff if the model reasons a similar amount again.
        turn_max_tokens = (
            min(MAX_COMPLETION_TOKENS * 2, CONTEXT_WINDOW)
            if length_retries else MAX_COMPLETION_TOKENS
        )
        try:
            with spin("thinking..."):
                response = _create_completion_with_fallback(
                    messages, round_tools_def, temperature=temperature,
                    system_prompt=round_system_prompt, on_token=on_token,
                    interrupt_check=interrupt_check, trace=trace, turn=turn,
                    max_tokens=turn_max_tokens,
                )
```

**Step 2: Branch the retry message and use `turn_max_tokens` in the warning**

Current:

```python
        finish_reason = str(getattr(response.choices[0], "finish_reason", "") or "").lower()
        if finish_reason in {"length", "max_tokens"}:
            warning = (
                f"Model output reached its {MAX_COMPLETION_TOKENS}-token limit. "
                "The partial response was not executed. Retry with one complete, "
                "concise tool call; split large work across calls if needed."
            )
            if on_result:
                on_result("error", warning)
            if trace is not None:
                trace.append({"turn": turn, "type": "max_tokens", "content": warning})
            if length_retries < 1:
                length_retries += 1
                messages.append({"role": "user", "content": warning})
                continue
            answer = _guard_answer(warning)
            if on_answer:
                on_answer(answer)
            return answer
```

New:

```python
        finish_reason = str(getattr(response.choices[0], "finish_reason", "") or "").lower()
        if finish_reason in {"length", "max_tokens"}:
            warning = (
                f"Model output reached its {turn_max_tokens}-token limit. "
                "The partial response was not executed."
            )
            if content:
                # A genuinely large answer/tool call was in progress.
                retry_instruction = (
                    f"{warning} Retry with one complete, concise tool call; "
                    "split large work across calls if needed."
                )
            else:
                # The whole budget was spent on reasoning before any answer or
                # tool call appeared — "split work into calls" doesn't address
                # that, so it reliably repeats the same failure.
                retry_instruction = (
                    f"{warning} That budget was spent entirely on reasoning "
                    "with no answer produced. Stop reasoning now and reply "
                    "immediately in plain text, or call one tool — do not "
                    "think out loud."
                )
            if on_result:
                on_result("error", warning)
            if trace is not None:
                trace.append({"turn": turn, "type": "max_tokens", "content": warning})
            if length_retries < 1:
                length_retries += 1
                messages.append({"role": "user", "content": retry_instruction})
                continue
            answer = _guard_answer(warning)
            if on_answer:
                on_answer(answer)
            return answer
```

Note `content` is already in scope from `content = _strip_reasoning(message.content or "")` a few lines above (`engine.py:6629`) — no new variable needed for Part A.

### Task 5: Verify GREEN

Run: `python3 -m pytest tests/test_length_retry.py -v`
Expected: all three tests **PASS**.

### Task 6: Full regression pass

Run: `python3 -m pytest tests/ -q`
Expected: no new failures versus the pre-change baseline (run the same command once before Task 3 to capture the baseline if unsure).

### Task 7: Commit

```bash
git add tests/conftest.py tests/test_length_retry.py src/agent8088/engine.py
git commit -m "fix: on a length cutoff, nudge correctly and double the retry's token budget"
```

## Risks, tradeoffs, and open questions

- **Still bounded.** `CONTEXT_WINDOW` (default 32768) caps the doubled retry budget, and only one retry is allowed — a model that reasons past even the doubled budget, or ignores the nudge entirely, will still fail. This raises the odds of success; it doesn't guarantee it. A follow-up (not in this plan) could make the retry budget escalate further or add a second retry specifically for the empty-content case.
- **Slightly higher worst-case latency/cost.** The retry can now use up to 2x the tokens (still capped by `CONTEXT_WINDOW`), so the worst case (both attempts hit their cap) costs more tokens/time than today's worst case. Judged acceptable since it only applies to the one retry after an already-failed attempt, and it replaces a guaranteed second failure with a good chance of success.
- **Test infra gap.** `tests/conftest.py` and most of `engine.py`'s test coverage are absent on this branch/`development` but present in other local worktrees (e.g. `.claude/worktrees/agent8088-readonly-docs-4b3520`) — those tests appear to have been dropped somewhere in this branch's history. That's a separate, pre-existing issue; Task 1 only adds the one fixture this plan's tests need, not a full restoration.
- **Open question:** should the "stop reasoning" nudge additionally set a provider-level reasoning-effort/think-off parameter (if the backend exposes one) instead of relying purely on a doubled budget plus a text instruction? Not implemented here — no such parameter currently exists anywhere in `engine.py`'s provider layer (verified via search), and adding one would need per-provider verification against a live Ollama model before it could be trusted. Flagging as a possible follow-up once this fix is verified in practice.
