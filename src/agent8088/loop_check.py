"""Is a long reply repeating itself? Deterministic measures, no model call.

A length cut-off alone cannot tell a reasoning loop from long, useful thinking. Repetition can. On the last 6,000
characters of Qwen3.8-27B reasoning blocks that ended on their own (507 blocks from long agentic coding sessions, 2026-11):

- zlib compression ratio: median 2.4, 99th percentile 3.1, highest 3.8
- share of unique 8-word sequences: median 1.0, 5th percentile 0.98, lowest 0.84

The one looping block measured 12.0 and 0.15. Text that repeats compresses well and reuses its word sequences.

Leaf module: nothing here imports the engine.
"""
from __future__ import annotations

import re
import zlib

TAIL_CHARS = 6000


def compression_ratio(text: str) -> float:
    """Bytes before zlib over bytes after: about 2-3 for ordinary prose, far higher for text that repeats."""
    data = str(text or "").encode("utf-8", "ignore")
    if not data:
        return 0.0
    return len(data) / max(1, len(zlib.compress(data, 6)))


def unique_ngram_share(text: str, n: int = 8) -> float:
    """Distinct n-word sequences over all of them: near 1 for ordinary prose, low when the same lines come back.

    Too short a text to judge counts as fully unique."""
    words = str(text or "").split()
    if len(words) < n + 50:
        return 1.0
    grams = [tuple(words[i:i + n]) for i in range(len(words) - n)]
    return len(set(grams)) / len(grams)


def tail_metrics(text: str, chars: int = TAIL_CHARS) -> dict:
    """The two measures on the last `chars` characters, rounded for a log record."""
    tail = str(text or "")[-chars:]
    return {"chars": len(str(text or "")), "cr_tail": round(compression_ratio(tail), 2),
            "u8_tail": round(unique_ngram_share(tail), 3)}


# --- the streaming check -------------------------------------------------------------------------------------------
#
# A reply is a loop only when both measures say so on two checks in a row, far outside what ordinary reasoning
# reached: a compression ratio of at least 6 (ordinary at most 3.8) and a unique 8-word share of at most 0.6
# (ordinary at least 0.84). Precision over recall: a false "loop" throws away good reasoning, which is the problem this
# replaces, while a missed loop costs one call that the output cap and the time budget still end.

LOOP_RATIO = 6.0
LOOP_UNIQUE = 0.6
START_CHARS = 16000          # about 4K tokens before the first check
EVERY_CHARS = 8000           # then about every 2K tokens
CONFIRM_CHECKS = 2

# Small models wander rather than repeat ("Wait, ...", "Actually, ..."). Counted for sensitive profiles and logged;
# no threshold acts on it until cut-off logs give one.
_DITHER = re.compile(r"\b(?:wait|actually|hmm+|let me (?:try|think|re-?check) again|no,? that(?:'s| is) (?:not|wrong))\b",
                     re.IGNORECASE)


def dither_rate(text: str) -> float:
    """Second-guessing phrases per 1,000 words of `text`."""
    words = len(str(text or "").split())
    return round(1000 * len(_DITHER.findall(str(text or ""))) / words, 1) if words else 0.0


class LoopWatch:
    """Fed the streamed reply piece by piece; says when it is a confirmed loop.

    The reasoning and the visible text are judged apart, since a reasoning model sends them in separate fields and
    either can loop on its own."""

    def __init__(self, sensitivity: str = "strict", ratio: float = LOOP_RATIO, unique: float = LOOP_UNIQUE,
                 start_chars: int = START_CHARS, every_chars: int = EVERY_CHARS):
        self.sensitivity = sensitivity
        self.ratio, self.unique = ratio, unique
        self.start_chars, self.every_chars = start_chars, every_chars
        self.reset()

    def reset(self) -> None:
        """A new stream (a retry or a fallback model) starts from nothing."""
        self._parts = {"reasoning": [], "content": []}
        self._chars = 0
        self._next = self.start_chars
        self._hits = 0
        self.checks: list[dict] = []
        self.confirmed: dict | None = None

    def text(self, kind: str) -> str:
        return "".join(self._parts[kind])

    def feed(self, kind: str, piece: str) -> bool:
        """Add a streamed piece; True once a loop is confirmed (and from then on)."""
        if self.confirmed is not None:
            return True
        if not piece:
            return False
        self._parts["reasoning" if kind == "reasoning" else "content"].append(piece)
        self._chars += len(piece)
        if self._chars >= self._next:
            self._next = self._chars + self.every_chars
            self._check()
        return self.confirmed is not None

    def _check(self) -> None:
        record = {"at_chars": self._chars}
        looping = ""
        for kind in ("reasoning", "content"):
            text = self.text(kind)
            if len(text) < TAIL_CHARS // 2:
                continue
            metrics = tail_metrics(text)
            if self.sensitivity == "sensitive":
                metrics["dither_per_1k_words"] = dither_rate(text[-TAIL_CHARS:])
            record[kind] = metrics
            if metrics["cr_tail"] >= self.ratio and metrics["u8_tail"] <= self.unique:
                looping = looping or kind
        record["looping"] = looping
        self.checks.append(record)
        self._hits = self._hits + 1 if looping else 0
        if self._hits >= CONFIRM_CHECKS:
            self.confirmed = {"in": looping, **record}

    def scan(self, reasoning: str, content: str, piece: int = 1000) -> dict | None:
        """The same checks on a reply that arrived whole (no streaming): fed in the order a stream would send it."""
        self.reset()
        for kind, text in (("reasoning", reasoning or ""), ("content", content or "")):
            for start in range(0, len(text), piece):
                if self.feed(kind, text[start:start + piece]):
                    return self.confirmed
        return self.confirmed
