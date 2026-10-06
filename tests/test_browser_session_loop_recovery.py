"""Browser interrupts must reach the caller without killing the shared loop."""
import asyncio
import concurrent.futures
import threading

import pytest

from agent8088 import browser_session as B


@pytest.fixture
def session_loop(monkeypatch):
    loop = asyncio.new_event_loop()
    started = threading.Event()
    loop.call_soon(started.set)
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    assert started.wait(2)
    monkeypatch.setattr(B, "get_session_event_loop", lambda: loop)
    try:
        yield loop
    finally:
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_interrupt_reaches_caller_and_the_session_loop_remains_usable(session_loop, error):
    async def interrupted():
        raise error()

    with pytest.raises(error):
        B.run_in_session_loop(interrupted(), timeout=0.2)

    async def next_call():
        return "still running"

    assert B.run_in_session_loop(next_call(), timeout=1) == "still running"


def test_timed_out_browser_work_is_cancelled(session_loop):
    cancelled = threading.Event()

    async def pending():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(concurrent.futures.TimeoutError):
        B.run_in_session_loop(pending(), timeout=0.1)
    assert cancelled.wait(1)
