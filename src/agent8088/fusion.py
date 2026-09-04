"""Fusion: one query, N models across N providers, blind-judged.

Runs entirely outside the normal agent loop -- no tools, no conversation
history. Pure library module; a CLI command built elsewhere calls into it.
"""
from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Callable, Optional

from agent8088 import engine as A

# Hard wall-clock bound on one fusion web_search. The engine's own tool
# timeout governs individual backend requests, but a slow backend chain
# (SearXNG down -> retries -> ddgs throttle backoff) can outlive a panel
# member's own timeout budget. 120s: enough for a throttled ddgs ladder,
# short enough that a member always answers within member_timeout_s.
FUSION_SEARCH_TIMEOUT_S = 120.0


PANEL_SYSTEM_PROMPT = (
    "You are one participant in a blind panel answering a single question. "
    "Answer directly and completely. You have one tool, web_search — call it "
    "for anything current, time-sensitive, or factual that could have changed "
    "since your training (events, releases, prices, winners, news). You may "
    "skip it only for pure reasoning, math, opinions, or knowledge that never "
    "changes. This is a one-shot consultation: budget at most a couple of "
    "searches, then give your final answer."
)

# Loop bound: enough rounds for a search, a refined search, and the final
# answer. ponytail: fixed 4, raise fusion_max_tool_turns if a member needs
# more follow-ups in practice.
MAX_TOOL_TURNS = 4

_ALLOWED_TOOLS = {"web_search"}
_ESCALATION_PREFIX = "ESCALATION_REQUEST"


def _run_tool_once(args: dict) -> str:
    """One web_search through the engine's own gated path — sensitive-query,
    secret-leak, SSRF and backend-chain guards all apply, same as the main
    loop. depth=1 keeps sub-delegation off. Bounded to
    FUSION_SEARCH_TIMEOUT_S in a worker thread: run_tool's own timeout
    governs each backend request, not the whole fallback chain, and a panel
    member must never hang past its own timeout budget on one search."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(A.run_tool, "web_search", args,
                             allow_plan=False, depth=1)
        try:
            return future.result(timeout=FUSION_SEARCH_TIMEOUT_S)
        except FuturesTimeoutError:
            future.cancel()
            return (f"Error: web_search timed out after "
                    f"{FUSION_SEARCH_TIMEOUT_S:.0f}s. Do not retry it — "
                    "answer from your own knowledge or the results you "
                    "already have.")
    finally:
        # No wait: a timed-out search must not hold the member hostage while
        # its worker thread drains. Python threads can't be killed, so the
        # underlying search may run to completion in the background -- the
        # member is free either way, and the pool thread exits with the
        # process.
        pool.shutdown(wait=False)


def _member_tool_loop(
    client,
    query: str,
    *,
    model: str,
    provider: str,
    max_tokens: int,
    completion_fn: CompletionFn,
) -> tuple:
    """Mini agent loop for one panel member: model may emit web_search calls
    (the ✿FUNCTION✿ content-channel protocol, parsed by find_tool_calls),
    results are appended as user messages, loop ends when it answers in plain
    text or the turn budget runs out.

    Returns (final_text, input_tokens, output_tokens). Escalation requests
    (permission prompts) are surfaced as a refusal to the model rather than
    answered — nobody is watching a panel member to approve it.
    """
    tool_docs = A.render_tool_docs(
        {name: A.TOOL_SPECS[name] for name in ("web_search",) if name in A.TOOL_SPECS}
    )
    # Same runtime context the main loop injects, most importantly today's
    # date: without it a model whose training predates an event is confident
    # the event hasn't happened, so the "search when time-sensitive" rule
    # never fires. Reuse the engine's block rather than a second date line.
    system_prompt = (PANEL_SYSTEM_PROMPT + "\n\n"
                      + A.render_runtime_context() + "\n" + tool_docs)
    messages = [{"role": "user", "content": query}]
    total_input = 0
    total_output = 0

    for _turn in range(MAX_TOOL_TURNS):
        response = completion_fn(
            client,
            messages,
            [],
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            temperature=0.3,
            on_token=None,
            interrupt_check=None,
            model_name=model,
            provider_name=provider,
            telemetry_attempt="fusion_panel",
        )
        text = A._strip_reasoning(response.choices[0].message.content or "")
        usage, _source = A._model_usage(response)
        total_input += usage.get("input_tokens") or 0
        total_output += usage.get("output_tokens") or 0

        calls = A.find_tool_calls(text, allowed=_ALLOWED_TOOLS)
        if not calls:
            # No runnable call. Two distinct causes, both must correct the
            # model rather than return leftover text as its "answer" — a live
            # run handed the judge raw ✿FUNCTION✿ protocol and half-sentences
            # as candidate answers.
            attempted = sorted(set(A._attempted_tool_names(text)) - _ALLOWED_TOOLS)
            cleaned = A.strip_tool_json(text)
            if attempted:
                # It tried a tool it doesn't have; find_tool_calls dropped it
                # silently. Say so, or the model believes the call ran.
                messages.append({"role": "user", "content":
                    f"Tools {', '.join(attempted)} are not available to you. "
                    "web_search is your only tool — use it, or answer in "
                    "plain text from what you already have."})
                continue
            if cleaned.strip():
                return cleaned, total_input, total_output
            messages.append({"role": "user", "content":
                "That tool call could not be executed (malformed or unsupported). "
                "Do not emit tool-call markup. Answer in plain text now, from "
                "what you have."})
            continue

        outcome_lines = []
        for call in calls:
            result = _run_tool_once(call.get("arguments") or {})
            if result.startswith(_ESCALATION_PREFIX):
                result = (
                    "Error: this search requires permission that nobody can "
                    "approve here. Do not retry it — answer from your own "
                    "knowledge or the results you already have."
                )
            outcome_lines.append(f"Result of web_search:\n{result}")
        messages.append({"role": "user", "content": "\n\n".join(outcome_lines)})

    # Budget exhausted: one last forced answer with no tools offered.
    messages.append({
        "role": "user",
        "content": "Search budget used up. Give your final answer now, in "
                   "plain text, with no further tool calls.",
    })
    response = completion_fn(
        client,
        messages,
        [],
        max_tokens=max_tokens,
        # No tool docs on the forced answer, but keep the date context:
        # an answer about a dated event is wrong by a year without it.
        system_prompt=PANEL_SYSTEM_PROMPT + "\n\n" + A.render_runtime_context(),
        temperature=0.3,
        on_token=None,
        interrupt_check=None,
        model_name=model,
        provider_name=provider,
        telemetry_attempt="fusion_panel",
    )
    text = A.strip_tool_json(A._strip_reasoning(
        response.choices[0].message.content or ""))
    usage, _source = A._model_usage(response)
    total_input += usage.get("input_tokens") or 0
    total_output += usage.get("output_tokens") or 0
    if not text.strip():
        text = "(no answer produced)"
    return text, total_input, total_output

JUDGE_SYSTEM_PROMPT = (
    "You are an impartial judge. You will see several answers to the same "
    "question, labeled anonymously. Pick the single best answer and justify "
    "your choice. You MUST always pick a winner — never refuse, never say "
    "you cannot decide, and never leave the WINNER line out. If the answers "
    "are similar or you are uncertain, pick the one you would trust most and "
    "say so in the verdict. Keep any thinking short: your output must start "
    "with the required format, not reasoning."
)

CompletionFn = Callable[..., object]


@dataclass
class PanelMember:
    provider: str
    model: str
    client: object


@dataclass
class PanelResult:
    member: PanelMember
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_s: float = 0.0
    error: Optional[str] = None  # None means success


@dataclass
class FusionResult:
    query: str
    results: list = field(default_factory=list)  # list[PanelResult], every member incl. failures
    winner_index: Optional[int] = None  # index into `results`
    winner_answer: str = ""
    verdict: str = ""
    judge_raw: str = ""
    judge_parsed: bool = False
    judge_error: Optional[str] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: Optional[float] = None


def discover_panel(max_panel_size: int = 6) -> list:
    """Walk engine.PROVIDERS in dict order, keeping providers with a working
    key and a configured model, deduped by backend, up to max_panel_size."""
    seen = set()
    panel: list[PanelMember] = []
    for name, info in A.PROVIDERS.items():
        if len(panel) >= max_panel_size:
            break
        if not isinstance(info, dict):
            continue
        model = info.get("model") or ""
        if not model:
            continue
        if not A._provider_api_key(info):
            continue
        base_url = info.get("base_url") or ""
        if info.get("api_mode") == "litellm":
            dedupe_key = ("litellm", base_url, model)
        else:
            dedupe_key = (base_url, model)
        if dedupe_key in seen:
            continue
        try:
            client, model_name = A.get_client(name)
        except Exception:
            continue
        seen.add(dedupe_key)
        panel.append(PanelMember(provider=name, model=model_name, client=client))
    return panel


def build_explicit_panel(specs: list) -> list:
    """Build a panel from explicit "provider" or "provider:model" strings.

    Unlike discover_panel, this trusts the caller's choice of provider and
    model rather than auto-discovering — used by `/fusion --panel ...` so a
    user can hand-pick exactly who answers. Raises ValueError naming the
    problem (unknown provider, no working key) rather than silently dropping
    an entry the user explicitly asked for."""
    panel: list[PanelMember] = []
    for spec in specs:
        spec = spec.strip()
        if not spec:
            continue
        if ":" in spec:
            provider, model = spec.split(":", 1)
        else:
            provider, model = spec, ""
        provider = provider.strip()
        model = model.strip()
        info = A.PROVIDERS.get(provider)
        if info is None:
            known = ", ".join(sorted(A.PROVIDERS)) or "(none configured)"
            raise ValueError(f"unknown provider '{provider}'. Known: {known}")
        if not A._provider_api_key(info):
            raise ValueError(f"provider '{provider}' has no working API key configured")
        if not model:
            model = info.get("model") or ""
        if not model:
            raise ValueError(f"provider '{provider}' has no model configured; specify one, e.g. '{provider}:some-model'")
        client, _default_model = A.get_client(provider)
        panel.append(PanelMember(provider=provider, model=model, client=client))
    return panel


def run_panel(
    panel: list,
    query: str,
    *,
    max_tokens: int = 1200,
    member_timeout_s: float = 60.0,
    max_workers: int = 8,
    completion_fn: CompletionFn = None,
    use_tools: bool = False,
) -> list:
    """Fan out one create_completion call per panel member in parallel.

    use_tools=True gives each member a mini tool loop with web_search —
    follow-up searches included — instead of a single no-tools answer."""
    if completion_fn is None:
        completion_fn = A.create_completion

    if not panel:
        return []

    messages = [{"role": "user", "content": query}]

    def _call(member: PanelMember) -> PanelResult:
        start = time.monotonic()
        try:
            if use_tools:
                text, input_tokens, output_tokens = _member_tool_loop(
                    member.client,
                    query,
                    model=member.model,
                    provider=member.provider,
                    max_tokens=max_tokens,
                    completion_fn=completion_fn,
                )
                return PanelResult(
                    member=member,
                    text=text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    elapsed_s=time.monotonic() - start,
                )
            response = completion_fn(
                member.client,
                messages,
                [],
                max_tokens=max_tokens,
                system_prompt=PANEL_SYSTEM_PROMPT,
                temperature=0.3,
                on_token=None,
                interrupt_check=None,
                model_name=member.model,
                provider_name=member.provider,
                telemetry_attempt="fusion_panel",
            )
            text = A.strip_tool_json(A._strip_reasoning(
                response.choices[0].message.content or ""))
            usage, _source = A._model_usage(response)
            return PanelResult(
                member=member,
                text=text,
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
                elapsed_s=time.monotonic() - start,
            )
        except Exception as exc:
            return PanelResult(
                member=member,
                elapsed_s=time.monotonic() - start,
                error=f"{type(exc).__name__}: {exc}",
            )

    workers = min(max_workers, len(panel) or 1)
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(_call, member): i for i, member in enumerate(panel)}
        results: list = [None] * len(panel)
        for future, idx in futures.items():
            member = panel[idx]
            try:
                results[idx] = future.result(timeout=member_timeout_s)
            except FuturesTimeoutError:
                results[idx] = PanelResult(
                    member=member,
                    elapsed_s=member_timeout_s,
                    error=f"TimeoutError: exceeded {member_timeout_s}s",
                )
            except Exception as exc:
                results[idx] = PanelResult(
                    member=member,
                    error=f"{type(exc).__name__}: {exc}",
                )
        return results
    finally:
        pool.shutdown(wait=False)


_WINNER_RE = re.compile(r"WINNER:\s*([A-Za-z])", re.IGNORECASE)
_VERDICT_RE = re.compile(r"VERDICT:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _parse_verdict(raw: str):
    """Loosely parse a judge response for WINNER:/VERDICT: markers."""
    winner_letter = None
    m = _WINNER_RE.search(raw)
    if m:
        winner_letter = m.group(1)
    v = _VERDICT_RE.search(raw)
    verdict_text = v.group(1).strip() if v else raw.strip()
    return winner_letter, verdict_text


def judge(
    query: str,
    survivors: list,
    judge_client: object,
    judge_model: str,
    judge_provider: str,
    *,
    max_tokens: int = 500,
    completion_fn: CompletionFn = None,
    rng: random.Random = None,
) -> "FusionResult":
    """Blind the survivors, ask the judge model to pick a winner.

    A judge whose max_tokens budget cuts it off before it emits its WINNER
    marker (reasoning models spend the budget thinking) is retried at 2x and
    then 4x the token budget. Reasoning tokens count against max_tokens, so
    a thoughtful judge can be starved of the room it needs to answer; the
    4x run observed live (glm-5.3, 500-token default) exhausted both prior
    attempts on thinking alone."""
    if completion_fn is None:
        completion_fn = A.create_completion

    result = FusionResult(query=query, results=survivors)

    order = list(range(len(survivors)))
    if rng is None:
        random.shuffle(order)
    else:
        rng.shuffle(order)
    labels = [chr(ord("A") + i) for i in range(len(order))]

    blocks = []
    for i, idx in enumerate(order):
        text = A._strip_reasoning(survivors[idx].text or "")
        blocks.append(f"Answer {labels[i]}:\n{text}")
    candidate_block = "\n\n".join(blocks)

    prompt = (
        f"Question:\n{query}\n\n{candidate_block}\n\n"
        "Pick the single best answer — one of the labeled letters. There is "
        "always a winner, even if the answers are close or all flawed; when "
        "uncertain, choose the most accurate, complete, and honest one. "
        "Respond in exactly this format, nothing else, with no preamble or "
        "reasoning before it:\n"
        "WINNER: <letter>\nVERDICT: <2-4 sentences explaining why this answer is best>"
    )

    def _call_judge(judge_max_tokens: int, urgency: str = ""):
        return completion_fn(
            judge_client,
            [{"role": "user", "content": prompt + urgency}],
            [],
            max_tokens=judge_max_tokens,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            temperature=0.0,
            on_token=None,
            interrupt_check=None,
            model_name=judge_model,
            provider_name=judge_provider,
            telemetry_attempt="fusion_judge",
        )

    total_input = 0
    total_output = 0
    raw = ""
    # Ladder: original budget, 2x, then 4x with a no-reasoning demand. A
    # reasoning judge can burn every prior budget thinking and never reach
    # WINNER:/VERDICT:. ponytail: fixed 3 attempts; not configurable until
    # a real case needs a fourth.
    urgencies = ("", "", "\n\nYou already have all the information you need. "
                       "Do NOT reason or deliberate. Your first characters "
                       "must be 'WINNER:'.")
    for budget, urgency in zip((max_tokens, max_tokens * 2, max_tokens * 4), urgencies):
        try:
            response = _call_judge(budget, urgency)
        except Exception as exc:
            result.judge_error = f"{type(exc).__name__}: {exc}"
            return result
        usage, _source = A._model_usage(response)
        total_input += usage.get("input_tokens") or 0
        total_output += usage.get("output_tokens") or 0
        raw = A._strip_reasoning(response.choices[0].message.content or "")
        if _WINNER_RE.search(raw):
            break

    result.judge_raw = raw
    result.total_input_tokens += total_input
    result.total_output_tokens += total_output

    winner_letter, verdict_text = _parse_verdict(raw)
    if winner_letter is None or (ord(winner_letter.upper()) - ord("A")) >= len(labels):
        result.judge_error = "could not parse judge output"
        return result

    label_pos = ord(winner_letter.upper()) - ord("A")
    original_idx = order[label_pos]
    result.winner_index = original_idx
    result.winner_answer = survivors[original_idx].text
    result.verdict = verdict_text
    result.judge_parsed = True
    return result


def run_fusion(
    query: str,
    *,
    panel: Optional[list] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    max_panel_size: int = 6,
    member_timeout_s: float = 60.0,
    max_workers: int = 8,
    max_tokens: int = 1200,
    judge_max_tokens: int = 500,
    completion_fn: CompletionFn = None,
    use_tools: bool = False,
) -> FusionResult:
    """Single entry point: discover panel, fan out, blind-judge, return.

    Pass `panel` (e.g. from build_explicit_panel) to skip auto-discovery and
    use exactly those members instead. use_tools=True gives every member a
    mini tool loop with web_search."""
    if completion_fn is None:
        completion_fn = A.create_completion

    if panel is None:
        panel = discover_panel(max_panel_size)
    if not panel:
        return FusionResult(
            query=query,
            results=[],
            judge_error="no providers with a working API key are configured",
        )

    results = run_panel(
        panel,
        query,
        max_tokens=max_tokens,
        member_timeout_s=member_timeout_s,
        max_workers=max_workers,
        completion_fn=completion_fn,
        use_tools=use_tools,
    )

    survivors = [r for r in results if r.error is None]

    if not survivors:
        return FusionResult(
            query=query,
            results=results,
            judge_error=f"all {len(panel)} panel members failed",
        )

    def _cost(total_input: int, total_output: int) -> Optional[float]:
        if not A.COST_PER_1K_INPUT and not A.COST_PER_1K_OUTPUT:
            return None
        return (total_input / 1000) * A.COST_PER_1K_INPUT + (total_output / 1000) * A.COST_PER_1K_OUTPUT

    if len(survivors) == 1:
        total_input = sum(r.input_tokens for r in results)
        total_output = sum(r.output_tokens for r in results)
        only = survivors[0]
        return FusionResult(
            query=query,
            results=results,
            winner_index=results.index(only),
            winner_answer=only.text,
            verdict=(
                f"only {only.member.provider}:{only.member.model} answered; "
                "the rest failed or timed out."
            ),
            judge_parsed=False,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost_usd=_cost(total_input, total_output),
        )

    resolved_judge_provider = (judge_provider or "").strip() or A.ACTIVE_PROVIDER or A.DEFAULT_PROVIDER
    resolved_judge_model = (judge_model or "").strip() or A.MODEL_NAME

    judge_client, judge_model_name = A.get_client(resolved_judge_provider)

    judge_result = judge(
        query,
        survivors,
        judge_client,
        resolved_judge_model or judge_model_name,
        resolved_judge_provider,
        max_tokens=judge_max_tokens,
        completion_fn=completion_fn,
    )

    if judge_result.winner_index is not None:
        remapped_winner_index = results.index(survivors[judge_result.winner_index])
    else:
        remapped_winner_index = None

    winner_index = remapped_winner_index
    winner_answer = judge_result.winner_answer
    verdict = judge_result.verdict

    if not judge_result.judge_parsed:
        first = survivors[0]
        winner_index = results.index(first)
        winner_answer = first.text

    total_input = sum(r.input_tokens for r in results) + judge_result.total_input_tokens
    total_output = sum(r.output_tokens for r in results) + judge_result.total_output_tokens

    return FusionResult(
        query=query,
        results=results,
        winner_index=winner_index,
        winner_answer=winner_answer,
        verdict=verdict,
        judge_raw=judge_result.judge_raw,
        judge_parsed=judge_result.judge_parsed,
        judge_error=judge_result.judge_error,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost_usd=_cost(total_input, total_output),
    )
