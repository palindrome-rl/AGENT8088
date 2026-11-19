"""Stop must end a document run promptly, not after the current chunk.

process() waited on one chunk's future with no interrupt check, and a chunk
can take a minute or more. Pressing Stop set the event, nothing observed it,
and the next message came back "A turn is already running. Stop it first" --
so Stop looked broken and the session was unusable until the run finished.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from agent8088 import document_access


def _source(tmp_path, chars=6000):
    target = tmp_path / "doc.txt"
    target.write_text("x" * chars, encoding="utf-8")
    return target


def test_stop_ends_the_run_while_a_chunk_is_still_in_flight(tmp_path):
    stop = threading.Event()

    def check():
        if stop.is_set():
            raise KeyboardInterrupt("stopped")

    def slow(task, passage):
        time.sleep(5)          # far longer than the test is willing to wait
        return "evidence"

    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        document_access.process(_source(tmp_path), "summarise", slow,
                                chunk_chars=1000, concurrency=2, check=check)
    assert time.monotonic() - started < 3, "Stop waited for the chunk to finish"


def test_an_uninterrupted_run_still_completes(tmp_path):
    result = document_access.process(_source(tmp_path, 2000), "summarise",
                                     lambda t, p: "evidence", chunk_chars=1000,
                                     concurrency=1)
    assert '"all_chunks_processed": true' in result.lower()


def test_the_interrupt_check_runs_repeatedly_while_waiting(tmp_path):
    """One check per committed chunk was not enough to notice a Stop."""
    calls = []

    def check():
        calls.append(1)

    document_access.process(_source(tmp_path, 2000), "summarise",
                            lambda t, p: (time.sleep(0.4), "evidence")[1],
                            chunk_chars=1000, concurrency=1, check=check)
    assert len(calls) > 4, f"check() ran only {len(calls)} times across a 0.8s run"
