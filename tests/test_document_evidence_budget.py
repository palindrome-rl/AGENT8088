"""Ask a chunk for what we keep, not six times more.

Each chunk was told it could write up to 8192 tokens, and its evidence is then
truncated to 5000 characters -- roughly 1250 tokens. On a 23 tok/s endpoint
that is about six minutes of generation per chunk, of which around 85 percent
is discarded, and it does not fit inside the default 120s request timeout.

The reserve held back in the context window is a separate quantity from the
amount actually requested. Lowering the request must not enlarge the chunk:
`usable = context - reserve`, so spending the two from one number made a
smaller ask silently produce bigger passages and longer reasoning.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent8088 import engine as A
from agent8088 import document_access


def test_the_request_is_sized_to_the_evidence_that_is_kept():
    _chunk, _read, requested = A._document_budgets(32768, 32768)
    kept_tokens = 5000 // A._DOCUMENT_CHARS_PER_TOKEN
    assert requested < A._DOCUMENT_OUTPUT_TOKENS
    assert requested > kept_tokens, "must still exceed what document_access keeps"


def test_the_request_covers_the_seven_hundred_word_instruction():
    """The chunk prompt says 'use at most 700 words'; 8192 never matched it."""
    _chunk, _read, requested = A._document_budgets(32768, 32768)
    assert requested >= 1400  # ~700 words with headroom


def test_a_smaller_request_does_not_enlarge_the_chunk():
    """usable = context - reserve. Spending both from one number meant a
    smaller ask produced a bigger passage, which is the opposite of the goal."""
    chunk, _read, requested = A._document_budgets(32768, 32768)
    assert requested < chunk // A._DOCUMENT_CHARS_PER_TOKEN
    assert chunk <= (32768 - A._DOCUMENT_OUTPUT_TOKENS
                     - A._DOCUMENT_OVERHEAD_TOKENS) * A._DOCUMENT_CHARS_PER_TOKEN


def test_chunk_sizing_is_unchanged_by_this_split():
    """The 87-page report must still fit under the chunk cap it fitted before."""
    chunk, _read, _requested = A._document_budgets(32768, 65000)
    assert chunk > 40000
    assert (374711 + chunk - 1) // chunk <= 48


def test_budgets_still_fit_every_context_size():
    for context in (8192, 32768, 128000, 200000):
        chunk, read_chars, requested = A._document_budgets(context, 65000)
        assert chunk >= 1000 and read_chars >= 1000 and requested >= 512
        assert chunk // A._DOCUMENT_CHARS_PER_TOKEN + requested < context


def test_a_tiny_completion_ceiling_still_caps_the_request():
    _chunk, _read, requested = A._document_budgets(8192, 700)
    assert requested <= 700


def test_the_retry_can_still_climb_back_when_the_smaller_ask_is_not_enough():
    """Capping the ask is safe only because reasoning exhaustion still buys a
    larger budget once, clamped to the model's own ceiling."""
    caps = []

    def call(cap):
        caps.append(cap)
        import types
        content = "" if len(caps) == 1 else "evidence"
        reason = "length" if len(caps) == 1 else "stop"
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content), finish_reason=reason)], usage=None)

    A._document_evidence(call, 2048, ceiling=32768)
    assert caps == [2048, 8192]
