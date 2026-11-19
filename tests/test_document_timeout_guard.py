"""Bounded, legible failure when a document is too big for the model.

A chunk asks for up to output_tokens of generation. On a slow endpoint that
takes longer than the per-request timeout, so every chunk request died at
timeout_seconds and was retried, silently, forever: the UI sat at "0/6 chunks
complete" with no error while nothing could ever succeed. Timeouts here are
deterministic -- the same request takes the same time -- so retrying is waste,
and the user needs the arithmetic, not a spinner.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import engine as A
from agent8088 import document_access


class _ProviderError(Exception):
    """A provider exception whose name and text say nothing about timing."""


# --- the message ------------------------------------------------------------

def test_the_guidance_states_the_numbers_that_caused_it():
    message = A._document_timeout_message(output_tokens=8192, timeout_seconds=120)
    assert "8192" in message
    assert "120" in message


def test_the_guidance_names_the_setting_to_change():
    message = A._document_timeout_message(output_tokens=8192, timeout_seconds=120)
    assert "timeout_seconds" in message


def test_the_guidance_does_not_tell_the_user_to_retry():
    """The old behaviour retried identically until the turn budget ran out."""
    message = A._document_timeout_message(output_tokens=8192, timeout_seconds=120)
    assert "try again" not in message.lower()
    assert "repeat the task" not in message.lower()


# --- classification ---------------------------------------------------------

def test_a_timeout_is_recognised_by_type_name():
    assert A._is_timeout_error(TimeoutError("deadline")) is True
    assert A._is_timeout_error(_ProviderError("connection reset")) is False


def test_a_timeout_is_recognised_by_message_when_the_type_is_opaque():
    """Providers wrap timeouts in their own exception classes."""
    assert A._is_timeout_error(Exception("Request timed out.")) is True
    assert A._is_timeout_error(Exception("APITimeoutError")) is True


def test_an_ordinary_error_is_not_a_timeout():
    assert A._is_timeout_error(ValueError("bad request")) is False


# --- the deadline is enforced while waiting, not only at chunk start --------

def test_processing_stops_at_its_deadline_even_mid_flight(tmp_path):
    """The budget was checked when a chunk started, so chunks already running
    could carry a run far past it. Six slow chunks overran by minutes."""
    source = tmp_path / "big.txt"
    source.write_text("x" * 6000, encoding="utf-8")

    def slow(task, passage):
        time.sleep(0.2)
        return "evidence"

    with pytest.raises(ValueError, match="budget"):
        document_access.process(source, "summarise", slow, chunk_chars=1000,
                                concurrency=1, deadline_seconds=0.3)


def test_a_run_inside_its_deadline_still_completes(tmp_path):
    source = tmp_path / "small.txt"
    source.write_text("x" * 2000, encoding="utf-8")
    result = document_access.process(source, "summarise", lambda t, p: "evidence",
                                     chunk_chars=1000, concurrency=1, deadline_seconds=30)
    assert '"all_chunks_processed": true' in result.lower()
