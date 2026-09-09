"""Reasoning models that spend their whole output budget thinking.

A model like qwen3 can consume every output token on hidden reasoning and
return empty content with finish_reason="length". document_access.process
then reports "returned empty evidence. Repeat the task to resume", but
repeating reproduces it exactly -- the budget is too small for this model,
not momentarily unlucky.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import engine as A


def _response(content, finish_reason="stop"):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=None)


# --- the predicate ----------------------------------------------------------

def test_empty_content_at_the_length_cap_is_reasoning_exhaustion():
    response = _response("", finish_reason="length")
    assert A._reasoning_exhausted_output(response, "") is True


def test_real_evidence_is_not_exhaustion():
    response = _response("Page 1: budget is 4200 tokens.", finish_reason="length")
    assert A._reasoning_exhausted_output(response, "Page 1: budget is 4200 tokens.") is False


def test_an_empty_answer_that_stopped_normally_is_a_different_problem():
    """finish_reason=stop with no text means the model chose to say nothing;
    a bigger budget will not change that, so it must not be retried."""
    assert A._reasoning_exhausted_output(_response("", finish_reason="stop"), "") is False


def test_a_runaway_think_block_that_hit_the_cap_counts_as_exhaustion():
    """_strip_reasoning drops an unclosed <think> tail, leaving "" behind."""
    raw = "<think>" + "considering the passage " * 50
    response = _response(raw, finish_reason="length")
    assert A._strip_reasoning(raw) == ""
    assert A._reasoning_exhausted_output(response, A._strip_reasoning(raw)) is True


# --- the retry --------------------------------------------------------------

def test_a_first_pass_answer_is_returned_without_a_second_call():
    caps = []

    def call(cap):
        caps.append(cap)
        return _response("Page 1: the budget is 4200 tokens.")

    assert "4200" in A._document_evidence(call, 2048)
    assert caps == [2048]


def test_exhaustion_retries_once_with_a_larger_budget():
    caps = []

    def call(cap):
        caps.append(cap)
        if len(caps) == 1:
            return _response("", finish_reason="length")
        return _response("Page 1: recovered evidence.")

    assert "recovered evidence" in A._document_evidence(call, 2048)
    assert caps == [2048, 2048 * A._DOCUMENT_REASONING_RETRY_FACTOR]


def test_a_second_exhaustion_fails_with_the_real_cause_not_repeat_the_task():
    def call(cap):
        return _response("", finish_reason="length")

    with pytest.raises(ValueError) as caught:
        A._document_evidence(call, 2048)
    message = str(caught.value)
    assert "reasoning" in message.lower()
    # The old message told the user to repeat, which reproduces the failure.
    assert "repeat the task" not in message.lower()


def test_an_ordinary_empty_answer_is_not_retried():
    caps = []

    def call(cap):
        caps.append(cap)
        return _response("", finish_reason="stop")

    assert A._document_evidence(call, 2048) == ""
    assert caps == [2048]
