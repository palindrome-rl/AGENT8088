"""Is a long reply repeating itself? Deterministic measures, no model call.

A length cut-off alone cannot tell a reasoning loop from long, useful thinking. Repetition can. On the last 6,000
characters of Qwen3.8-27B reasoning blocks that ended on their own (507 blocks from long agentic coding sessions, 2026-11):

- zlib compression ratio: median 2.4, 99th percentile 3.1, highest 3.8
- share of unique 8-word sequences: median 1.0, 5th percentile 0.98, lowest 0.84

The one looping block measured 12.0 and 0.15. Text that repeats compresses well and reuses its word sequences.

Leaf module: nothing here imports the engine.
"""
from __future__ import annotations

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
