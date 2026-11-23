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


# Family, version and size, with any separators and anything before or after: provider paths (`Qwen/`,
# `openai/`), tags (`:27b`, `:latest`), variants (`-instruct`, `-it`, `-AWQ`) and file names (`-Q4_K_M.gguf`).
KNOWN_MODELS = (
    KnownModel("qwen3.8-27b",
               re.compile(r"qwen[\s_.-]*3[._-]?8(?![0-9])[^0-9]{0,24}?(?<![0-9.])27(?:\.0)?\s*b(?![a-z0-9])",
                          re.IGNORECASE),
               QWEN38_27B),
    KnownModel("gemma-4-12b",
               re.compile(r"gemma[\s_.-]*4(?![0-9.])[^0-9]{0,24}?(?<![0-9.])12(?:\.0)?\s*b(?![a-z0-9])",
                          re.IGNORECASE),
               GEMMA4_12B),
)


@dataclass(frozen=True)
class Detection:
    profile: OutputProfile
    source: str                 # "known", "size" or "unknown size"
    matched: str                # the known model, or "larger than 15B" / "15B or smaller" / "no size in the name"
    size_b: float | None        # parameters in billions read from the name, when there was one


# A parameter count in a name: 27b, 12B, 0.6b, 1.5B, 270m, 2.4t, 8x7b. A count right after a letter is not the
# model's size (a3b and e4b are active or effective parameters, a sub-part of a larger model).
_SIZE = re.compile(r"(?<![a-z0-9.])(?:(\d+)\s*x\s*)?(\d+(?:\.\d+)?)\s*([bmt])(?![a-z0-9])", re.IGNORECASE)
_UNIT = {"m": 0.001, "b": 1.0, "t": 1000.0}


def parameter_count(model: str) -> float | None:
    """Billions of parameters named in a model id, the largest one when several appear, or None."""
    sizes = []
    for experts, number, unit in _SIZE.findall(str(model or "")):
        size = float(number) * _UNIT[unit.lower()] * (int(experts) if experts else 1)
        if size > 0:
            sizes.append(size)
    return max(sizes) if sizes else None


def detect(model: str) -> Detection:
    """The output limits for a served model id, and how they were found."""
    name = str(model or "")
    for known in KNOWN_MODELS:
        if known.pattern.search(name):
            return Detection(known.profile, "known", known.name, parameter_count(name))
    size = parameter_count(name)
    if size is None:
        return Detection(fallback(QWEN38_27B), "unknown size", "no size in the name", None)
    if size > SMALL_MODEL_MAX_B:
        return Detection(fallback(QWEN38_27B), "size", "larger than 15B", size)
    return Detection(fallback(GEMMA4_12B), "size", "15B or smaller", size)


def fallback(profile: OutputProfile) -> OutputProfile:
    """A known model's limits used for another model: the same numbers, marked uncalibrated."""
    return OutputProfile(profile.thinking_cap, profile.nonthinking_cap, profile.write_cap, profile.loop_sensitivity,
                         False, f"uncalibrated: the limits of {profile.note.split(' on ')[0]}")


def describe(model: str, detection: Detection) -> str:
    """One line for the log and the trace."""
    profile = detection.profile
    how = (f"known model {detection.matched}" if detection.source == "known"
           else f"{detection.matched}" + (f" ({detection.size_b:g}B)" if detection.size_b else ""))
    return (f"output limits for {model or 'the model'}: {profile.thinking_cap} tokens with thinking, "
            f"{profile.nonthinking_cap} without ({how}{'' if profile.calibrated else ', uncalibrated'})")
