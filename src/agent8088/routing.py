"""Auto model routing -- an opt-in strength ladder over the existing fallback path.

Two axes, deliberately kept apart:

  horizontal  The model is *unreachable* (429/5xx/timeout). engine's existing
              `fallback_models` chain picks an alternative. Untouched by this module.
  vertical    The model is *reachable but failing the task*. Climb `auto_chain`
              to a stronger rung. That is what this module decides.

Conflating the two would make one of them wrong: a fallback list is ordered by
preference, a ladder is ordered by strength.

This module is pure -- parsing, ordering, cooldown bookkeeping, rung selection.
It imports nothing from `engine`, which is what lets `engine` import it. Anything
needing live provider state (discovery, seeding the chain) lives in `cli.py`.

Everything here is inert unless the user selects the `auto` model.
"""
from __future__ import annotations

import re
import time

from agent8088 import model_catalog

# Ordered strongest-hint first. Used ONCE, when seeding a chain in `/model auto
# setup`, and never at run time -- which is the whole difference from the
# MODEL_TIERS table that was deleted in the 2026-08-28 subagent redesign. A
# stale guess here costs the user one edit of auto_chain, not a broken route.
_STRENGTH_HINTS: list[tuple[str, int]] = [
    (r"opus|deepseek-reasoner|reasoner|o[13]-|gpt-5", 100),
    (r"sonnet|gpt-4o|gpt-4\.|-405b|-235b|qwen-max|kimi-k2|glm-4", 80),
    (r"-120b|-70b|large|pro\b|medium", 60),
    (r"-32b|-30b|plus\b|mixtral", 45),
    (r"-14b|-8b|-9b|small|flash|mini|haiku|instant|nano|lite", 20),
]
_DEFAULT_STRENGTH = 50


def is_auto(model_name: str) -> bool:
    """True for `auto`, `auto:fast`, `auto:smart`."""
    return str(model_name or "").strip().lower().split(":", 1)[0] == "auto"


def variant(model_name: str) -> str:
    """`auto:smart` -> "smart". Bare `auto` -> "". Unknown variants -> ""."""
    _, _, suffix = str(model_name or "").strip().lower().partition(":")
    return suffix if suffix in ("fast", "smart") else ""


def parse_chain(raw: str, known_providers=None) -> list[tuple[str, str]]:
    """Parse `provider:model,provider:model` into ordered (provider, model) pairs.

    Same grammar as engine's `fallback_models` so a user only learns one format.
    Entries naming an unknown provider are dropped rather than raising: a chain
    that mentions a provider the user later removed must degrade, not brick the
    session. Order is preserved and duplicates are dropped (first wins).
    """
    out: list[tuple[str, str]] = []
    seen = set()
    for item in str(raw or "").split(","):
        provider, sep, model = item.strip().partition(":")
        provider, model = provider.strip(), model.strip()
        if not sep or not provider or not model:
            continue
        if known_providers is not None and provider not in known_providers:
            continue
        if (provider, model) in seen:
            continue
        seen.add((provider, model))
        out.append((provider, model))
    return out


def starting_rung(model_name: str, chain_len: int) -> int:
    """Where `auto` begins on the ladder.

    `auto` and `auto:fast` start cheap; `auto:smart` starts at the top so a user
    who already knows the work is hard doesn't pay for a failed cheap attempt.
    """
    if chain_len <= 0:
        return 0
    return chain_len - 1 if variant(model_name) == "smart" else 0


_PARAM_COUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[bB](?:\b|[:_-])")

# Threshold -> score, checked highest-first. Below the lowest threshold falls
# through to a fixed floor rather than another tuple, since there is no
# meaningful lower bound on parameter count.
_PARAM_TIERS: list[tuple[float, int]] = [
    (100.0, 90), (30.0, 70), (10.0, 50), (3.0, 30),
]


def _param_count_score(text: str) -> int | None:
    """Parse a parameter count out of a model id (30b, 70B, 1.5b) and map it
    to a strength tier. None if no count is present -- distinct from a real
    tier match, so the caller can combine this with _keyword_score."""
    match = _PARAM_COUNT_RE.search(text)
    if not match:
        return None
    count = float(match.group(1))
    for threshold, score in _PARAM_TIERS:
        if count >= threshold:
            return score
    return 15


def _keyword_score(text: str) -> int | None:
    """Match today's name-substring tiers. None if nothing matches -- this is
    what changed from the old rank_hint: it used to return _DEFAULT_STRENGTH
    here, which made "no signal" indistinguishable from "matched the default
    tier" and made combining two heuristics impossible."""
    for pattern, score in _STRENGTH_HINTS:
        if re.search(pattern, text):
            return score
    return None


def rank_hint(provider: str, model_id: str) -> int:
    """Strength score for a provider:model pair.

    Checks the maintained catalog (bundled + user override, model_catalog.py)
    first. Only when neither has an entry does it fall back to guessing from
    the name: a parsed parameter count and today's keyword tiers, taking
    whichever signal is stronger. Seeding only -- consulted by order_for_seed
    when building a proposed auto_chain, never at routing time.
    """
    entry = model_catalog.lookup(provider, model_id)
    if entry is not None:
        return int(entry.get("rank", _DEFAULT_STRENGTH))
    text = str(model_id or "").lower()
    candidates = [s for s in (_param_count_score(text), _keyword_score(text))
                 if s is not None]
    return max(candidates) if candidates else _DEFAULT_STRENGTH


def order_for_seed(candidates):
    """Order (provider, model) pairs weakest-first for a proposed auto_chain.

    Sorted by hint ascending so the cheap rung comes first and escalation moves
    right. Ties keep discovery order, which is the user's provider ordering.
    """
    return [c for _, _, c in
            sorted(((rank_hint(p, m), i, (p, m))
                    for i, (p, m) in enumerate(candidates)),
                   key=lambda t: (t[0], t[1]))]


# --- cooldowns -------------------------------------------------------------
# Process-local on purpose. A cooldown is a statement about right now; carrying
# one across a restart would suppress a model that is very likely healthy again.
_cooldowns: dict[tuple[str, str], float] = {}


def mark_cooldown(provider: str, model: str, seconds: float) -> None:
    if seconds and seconds > 0:
        _cooldowns[(provider, model)] = time.time() + float(seconds)


def cooling(provider: str, model: str, now: float | None = None) -> bool:
    until = _cooldowns.get((provider, model))
    if until is None:
        return False
    if (now if now is not None else time.time()) >= until:
        _cooldowns.pop((provider, model), None)
        return False
    return True


def clear_cooldowns() -> None:
    _cooldowns.clear()


# --- selection -------------------------------------------------------------
def select(chain, rung: int, fits=None, now: float | None = None):
    """Pick a usable rung at or above `rung`.

    Returns (index, provider, model), or None when nothing qualifies.

    Skips anything cooling down or that `fits` rejects -- `fits(provider, model)`
    is how the caller enforces "this model's context window can hold the current
    payload", which matters because escalating onto a *smaller*-window model
    would overflow. Searching upward first keeps escalation monotonic; if nothing
    above qualifies we fall back to scanning from the bottom so a cooling top
    rung cannot strand the turn with no model at all.
    """
    if not chain:
        return None
    order = list(range(max(0, rung), len(chain))) + list(range(0, max(0, rung)))
    for index in order:
        provider, model = chain[index]
        if cooling(provider, model, now=now):
            continue
        if fits is not None and not fits(provider, model):
            continue
        return index, provider, model
    return None


def quality_failure(*, length_retries: int = 0, parse_error_retries: int = 0,
                    missing_args_retries: int = 0, unknown_retries: int = 0,
                    forcing: bool = False) -> str:
    """Name the struggle signal that justifies escalating, or "" for none.

    These counters are already maintained by the agent loop, so escalation reads
    evidence the run has actually produced rather than asking a model to predict
    its own difficulty -- a judgement models are poorly calibrated for, and one
    the weakest model in the chain would be making.
    """
    if length_retries >= 1:
        return "length_cutoff"
    if parse_error_retries + missing_args_retries >= 2:
        return "invalid_tool_calls"
    if unknown_retries >= 2:
        return "unknown_tools"
    if forcing:
        return "no_progress"
    return ""
