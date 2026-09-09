"""Exactly one system message, always at index 0.

Auto-compaction rewrites the history as [system("Conversation summary: ..."),
*recent], and _create_completion then prepends the real system prompt. The
request went out with two system messages, the summary sitting at index 1, and
strict OpenAI-compatible servers reject that: "Error code: 400 - {'error':
{'message': 'System message must be at the beginning.'}}". Every turn after a
compaction failed, on any long conversation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent8088 import engine as A


def _roles(messages):
    return [m["role"] for m in messages]


def test_a_plain_history_gets_one_leading_system_message():
    out = A._normalised_messages("SYSTEM", [{"role": "user", "content": "hi"}])
    assert _roles(out) == ["system", "user"]
    assert out[0]["content"] == "SYSTEM"


def test_a_compaction_summary_does_not_become_a_second_system_message():
    history = [{"role": "system", "content": "Conversation summary:\nWe fixed OCR."},
               {"role": "user", "content": "carry on"}]
    out = A._normalised_messages("SYSTEM", history)
    assert _roles(out).count("system") == 1
    assert _roles(out)[0] == "system"


def test_the_summary_text_survives_the_merge():
    """Dropping it would silently lose everything the compaction preserved."""
    history = [{"role": "system", "content": "Conversation summary:\nWe fixed OCR."},
               {"role": "user", "content": "carry on"}]
    out = A._normalised_messages("SYSTEM", history)
    assert "We fixed OCR." in out[0]["content"]
    assert "SYSTEM" in out[0]["content"]


def test_a_system_message_deeper_in_the_history_is_also_folded_up():
    history = [{"role": "user", "content": "one"},
               {"role": "system", "content": "mid-conversation note"},
               {"role": "assistant", "content": "two"}]
    out = A._normalised_messages("SYSTEM", history)
    assert _roles(out) == ["system", "user", "assistant"]
    assert "mid-conversation note" in out[0]["content"]


def test_conversation_order_is_preserved():
    history = [{"role": "user", "content": "one"},
               {"role": "assistant", "content": "two"},
               {"role": "user", "content": "three"}]
    out = A._normalised_messages("SYSTEM", history)
    assert [m["content"] for m in out[1:]] == ["one", "two", "three"]


def test_the_original_history_is_not_mutated():
    history = [{"role": "system", "content": "summary"}, {"role": "user", "content": "hi"}]
    A._normalised_messages("SYSTEM", history)
    assert _roles(history) == ["system", "user"]


def test_multimodal_content_is_left_alone():
    """A /image message's content is a list of parts, not a string."""
    parts = [{"type": "text", "text": "what is this"}]
    out = A._normalised_messages("SYSTEM", [{"role": "user", "content": parts}])
    assert out[1]["content"] is parts
