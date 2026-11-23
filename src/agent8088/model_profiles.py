"""Output limits per model: how long one reply may run before the harness stops it.

A thinking model spends most of a reply reasoning, and how long that reasoning may usefully run depends on the
model. Measured in long agentic coding sessions (2026-11):

- Qwen3.8-27B (vLLM): finished reasoning runs long and is worth keeping. Of 508 long reasoning blocks, 11.6% passed
  8K tokens and 4.3% passed 16K; at 20K about 98% fit. The old cap sat at 8,192 on 91% of calls and cut off 4.9% of
  calls, about 4.6 hours of a run.
- Gemma 4 12B (Ollama): a reply that reaches 8K tokens has mostly lost its way. Of calls sent with a 16K cap, 19%
  of those reaching 8K finished, and 1% of those reaching 15K. 10K keeps the replies that end on their own.

A served model is matched by its name, wherever it runs: a local server or the user's own, any provider prefix,
tag or quantisation suffix (`qwen3.8:27b`, `Qwen/Qwen3.8-27B-AWQ`, `gemma-4-12B-it-Q4_K_M.gguf`). A model that is
not one of these is sized from its name: above 15B it gets the Qwen3.8-27B limits, at or below 15B the Gemma 4 12B
limits. With no size in the name, it gets the larger model's limits: a cap that is too high costs one bounded call
on a runaway, while one that is too low throws away good reasoning on every long reply. Limits found by size are
marked uncalibrated.

Leaf module: nothing here imports the engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SMALL_MODEL_MAX_B = 15.0      # at or below this many billion parameters, the small-model limits apply


@dataclass(frozen=True)
class OutputProfile:
    thinking_cap: int           # cap for an ordinary call, thinking on
    nonthinking_cap: int        # cap for a call with thinking off, and the floor after a cut-off
    write_cap: int              # the largest cap a cut-off file write may earn on its retry
    loop_sensitivity: str       # "strict" (only literal repetition is a loop) or "sensitive" (small models)
    calibrated: bool            # measured for this model, not a fallback
    note: str                   # where the numbers come from


QWEN38_27B = OutputProfile(
    thinking_cap=20000, nonthinking_cap=8192, write_cap=16384, loop_sensitivity="strict", calibrated=True,
    note="Qwen3.8-27B on vLLM, measured in long agentic coding sessions, 2026-11")
GEMMA4_12B = OutputProfile(
    thinking_cap=10000, nonthinking_cap=8192, write_cap=16384, loop_sensitivity="sensitive", calibrated=True,
    note="Gemma 4 12B on Ollama, measured in long agentic coding sessions, 2026-11")


@dataclass(frozen=True)
class KnownModel:
    name: str
    pattern: re.Pattern
    profile: OutputProfile
    thinking: bool              # whether the model reasons before it answers


# Family, version and size, with any separators and anything before or after: provider paths (`Qwen/`,
# `openai/`), tags (`:27b`, `:latest`), variants (`-instruct`, `-it`, `-AWQ`) and file names (`-Q4_K_M.gguf`).
KNOWN_MODELS = (
    KnownModel("qwen3.8-27b",
               re.compile(r"qwen[\s_.-]*3[._-]?8(?![0-9])[^0-9]{0,24}?(?<![0-9.])27(?:\.0)?\s*b(?![a-z0-9])",
                          re.IGNORECASE),
               QWEN38_27B, thinking=True),
    KnownModel("gemma-4-12b",
               re.compile(r"gemma[\s_.-]*4(?![0-9.])[^0-9]{0,24}?(?<![0-9.])12(?:\.0)?\s*b(?![a-z0-9])",
                          re.IGNORECASE),
               GEMMA4_12B, thinking=True),
)


@dataclass(frozen=True)
class Detection:
    profile: OutputProfile
    source: str                 # "known", "size" or "unknown size"
    matched: str                # the known model, or "larger than 15B" / "15B or smaller" / "no size in the name"
    size_b: float | None        # total parameters in billions read from the name, when there was one
    active_b: float | None = None       # active parameters of a mixture-of-experts model (`-a5b`), when named
    thinking: bool | None = None        # whether the model reasons before it answers; None while unknown
    thinking_source: str = "unknown"    # "known", "name", "config", "endpoint", "replies" or "unknown"

    def cap(self) -> int:
        """The cap of an ordinary call: the thinking cap unless the model is known not to think. While unknown,
        the larger cap, for the reason the larger limits are the default."""
        return self.profile.nonthinking_cap if self.thinking is False else self.profile.thinking_cap


# A parameter count in a name: 27b, 12B, 0.6b, 1.5B, 270m, 2.4t, 8x7b. A count right after a letter is a part of a
# larger model, not its size: a5b is the active part of a mixture of experts, e4b the effective size of a model that
# loads more. Counted apart, below.
_SIZE = re.compile(r"(?<![a-z0-9.])(?:(\d+)\s*x\s*)?(\d+(?:\.\d+)?)\s*([bmt])(?![a-z0-9])", re.IGNORECASE)
_ACTIVE = re.compile(r"(?<![a-z0-9.])a(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", re.IGNORECASE)
_EFFECTIVE = re.compile(r"(?<![a-z0-9.])e(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", re.IGNORECASE)
_UNIT = {"m": 0.001, "b": 1.0, "t": 1000.0}
# Below this many millions an `m` is a context length (`-1m`, `-32m`), not a model: the smallest models named in
# millions are around 100M.
_MIN_MILLIONS = 50


def parameter_counts(model: str) -> tuple[float | None, float | None]:
    """(total, active) billions of parameters named in a model id, or None for what it does not say.

    The total is the largest plain count (`26b` in `26b-a5b`, `176` for `8x22b`). A name that gives only an
    effective size (`gemma-3n-e4b`) is sized by it. The active part is the `a`-prefixed count of a mixture of
    experts."""
    name = str(model or "")
    sizes = []
    for experts, number, unit in _SIZE.findall(name):
        if unit.lower() == "m" and float(number) < _MIN_MILLIONS:
            continue
        size = float(number) * _UNIT[unit.lower()] * (int(experts) if experts else 1)
        if size > 0:
            sizes.append(size)
    total = max(sizes) if sizes else None
    if total is None:
        effective = [float(n) for n in _EFFECTIVE.findall(name) if float(n) > 0]
        total = max(effective) if effective else None
    active = [float(n) for n in _ACTIVE.findall(name) if float(n) > 0]
    return total, (max(active) if active else None)


def parameter_count(model: str) -> float | None:
    """Billions of parameters named in a model id (the total), or None."""
    return parameter_counts(model)[0]


# What a name says about thinking. A name that says neither is unknown, not "no": most model names do not say.
_NOT_THINKING = re.compile(r"no[\s_-]?think|non[\s_-]?(?:thinking|reasoning)|instruct[\s_-]?2507", re.IGNORECASE)
_THINKING = re.compile(r"think|reason(?:ing|er)?(?![a-z])|(?<![a-z0-9])r1(?![0-9])|(?<![a-z])qwq(?![a-z])",
                       re.IGNORECASE)


def thinking_from_name(model: str) -> bool | None:
    """True when the name says the model reasons first, False when it says it does not, else None."""
    name = str(model or "")
    if _NOT_THINKING.search(name):
        return False
    if _THINKING.search(name):
        return True
    return None


def detect(model: str, thinking: bool | None = None, thinking_source: str = "") -> Detection:
    """The output limits for a served model id, and what is known about it.

    `thinking`, with its source, is what the caller learned elsewhere (the user's setting, the endpoint, the
    replies); it is used when the known model or the name does not already say."""
    name = str(model or "")
    total, active = parameter_counts(name)
    for known in KNOWN_MODELS:
        if known.pattern.search(name):
            return Detection(known.profile, "known", known.name, total, active, known.thinking, "known")
    named = thinking_from_name(name)
    if named is not None:
        thinking, thinking_source = named, "name"
    elif thinking is None:
        thinking_source = "unknown"
    if total is None:
        return Detection(fallback(QWEN38_27B), "unknown size", "no size in the name", None, active,
                         thinking, thinking_source or "unknown")
    if total > SMALL_MODEL_MAX_B:
        return Detection(fallback(QWEN38_27B), "size", "larger than 15B", total, active, thinking,
                         thinking_source or "unknown")
    return Detection(fallback(GEMMA4_12B), "size", "15B or smaller", total, active, thinking,
                     thinking_source or "unknown")


def fallback(profile: OutputProfile) -> OutputProfile:
    """A known model's limits used for another model: the same numbers, marked uncalibrated."""
    return OutputProfile(profile.thinking_cap, profile.nonthinking_cap, profile.write_cap, profile.loop_sensitivity,
                         False, f"uncalibrated: the limits of {profile.note.split(' on ')[0]}")


def _size_text(detection: Detection) -> str:
    if detection.size_b is None:
        return "size unknown"
    text = f"{detection.size_b:g}B"
    return text + (f", {detection.active_b:g}B active" if detection.active_b else "")


def describe(model: str, detection: Detection) -> str:
    """One line for the log and the trace: the model, its size, whether it thinks, and the cap that follows."""
    profile = detection.profile
    how = f"known model {detection.matched}" if detection.source == "known" else detection.matched
    thinks = {True: "thinks", False: "does not think", None: "thinking unknown"}[detection.thinking]
    if detection.thinking is not None and detection.thinking_source not in ("known", "unknown"):
        thinks += f" (from the {detection.thinking_source})"
    return (f"output limits for {model or 'the model'}: {detection.cap()} tokens per call, {profile.nonthinking_cap} "
            f"with thinking off ({how}; {_size_text(detection)}; {thinks}"
            f"{'' if profile.calibrated else '; uncalibrated'})")
