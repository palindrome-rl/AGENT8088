"""The answer shown when the model call fails after a tool has already run.

Pasting the raw tool result was indistinguishable from an answer: a
document_read result arrived in chat as a truncated JSON envelope starting
`{"version": ...}`, cut off mid-sentence at 1000 characters, with nothing
saying the model had failed. Two failed turns in a row produced two identical
blobs, which read as the agent ignoring the question.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent8088 import engine as A


def _document_result(text="Section 2.11 covers graph engineering."):
    return json.dumps({"version": "29900f69bb0d0c6b", "characters": 374711,
                       "source": "harness.pdf", "start": 145116, "end": 209116,
                       "next_cursor": 209116, "text": text})


def test_no_tool_output_reports_the_backend_error():
    answer = A._fallback_answer("", "connection reset")
    assert "connection reset" in answer


def test_the_json_envelope_never_reaches_the_user():
    answer = A._fallback_answer(_document_result(), "context length exceeded")
    assert '{"version"' not in answer
    assert "next_cursor" not in answer
    assert "145116" not in answer


def test_the_document_text_is_kept_and_the_failure_is_stated():
    answer = A._fallback_answer(_document_result(), "context length exceeded")
    assert "Section 2.11 covers graph engineering." in answer
    assert "context length exceeded" in answer


def test_the_user_is_told_the_text_is_unsummarised():
    """Without this the excerpt reads as the agent's own answer to the question."""
    answer = A._fallback_answer(_document_result(), "boom")
    assert "could not" in answer.lower()
    assert "unsummarised" in answer.lower() or "raw" in answer.lower()


def test_non_json_tool_output_is_still_labelled_not_pasted_bare():
    answer = A._fallback_answer("plain shell output here", "boom")
    assert "plain shell output here" in answer
    assert "boom" in answer
    assert not answer.startswith("plain shell output")


def test_long_output_is_bounded():
    answer = A._fallback_answer(_document_result("x" * 50000), "boom")
    assert len(answer) < 2500


def test_malformed_json_does_not_raise():
    answer = A._fallback_answer('{"text": "unterminated', "boom")
    assert "boom" in answer
