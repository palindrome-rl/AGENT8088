"""Web UI fallback visibility: the `capabilities` websocket push and
/api/status's model_served field (the header badge and the status bar read
them)."""
import asyncio
import threading

import pytest

from agent8088 import capabilities as C


@pytest.fixture
def cli(engine):
    from agent8088 import cli as cli_mod
    return cli_mod


class _FakeSocket:
    def __init__(self, fail=False):
        self.frames = []
        self.fail = fail

    async def send_json(self, frame):
        if self.fail:
            raise RuntimeError("socket closed")
        self.frames.append(frame)


def test_a_state_change_on_another_thread_is_pushed_to_every_socket(monkeypatch):
    from agent8088 import web_server
    good, broken = _FakeSocket(), _FakeSocket(fail=True)
    monkeypatch.setattr(web_server.manager, "active", [broken, good])

    async def scenario():
        unsubscribe = web_server._subscribe_capability_broadcast(asyncio.get_running_loop())
        try:
            # report() from a worker thread, like a search falling back mid-turn.
            worker = threading.Thread(target=lambda: C.report(
                C.SEARCH, active="ddgs", preferred="searxng", state=C.DEGRADED,
                reason="SearXNG not answering"))
            worker.start()
            worker.join()
            for _ in range(50):
                if good.frames:
                    break
                await asyncio.sleep(0.01)
            # An unchanged re-report is not a change, so no second frame.
            C.report(C.SEARCH, active="ddgs", state=C.DEGRADED)
            await asyncio.sleep(0.02)
        finally:
            unsubscribe()

    asyncio.run(scenario())
    assert len(good.frames) == 1
    frame = good.frames[0]
    assert frame["type"] == "capabilities"
    assert frame["data"][0]["name"] == "search"
    assert frame["data"][0]["state"] == "degraded"


def test_unsubscribed_after_shutdown():
    from agent8088 import web_server
    loop = asyncio.new_event_loop()
    unsubscribe = web_server._subscribe_capability_broadcast(loop)
    assert C.has_subscribers()
    unsubscribe()
    assert not C.has_subscribers()
    loop.close()


def test_a_closed_loop_is_ignored():
    from agent8088 import web_server
    loop = asyncio.new_event_loop()
    unsubscribe = web_server._subscribe_capability_broadcast(loop)
    loop.close()
    try:
        C.report(C.OCR, active="", state=C.UNAVAILABLE)   # must not raise
    finally:
        unsubscribe()


def test_api_status_reports_the_model_actually_answering(cli, monkeypatch):
    from fastapi.testclient import TestClient
    from agent8088 import web_server
    monkeypatch.setattr(web_server, "_eng", lambda: cli.A)
    monkeypatch.setattr(web_server, "_cl", lambda: cli)
    monkeypatch.setattr(cli.A, "LAST_MODEL_SERVED",
                        {"provider": "ollama", "model": "qwen3:8b",
                         "fallback_for": "openai:gpt-5", "reason": "Can't reach the API"})
    served = TestClient(web_server.app).get("/api/status").json()["model_served"]
    assert served == {"provider": "ollama", "model": "qwen3:8b",
                      "fallback_for": "openai:gpt-5", "reason": "Can't reach the API"}


def test_api_status_model_served_is_empty_before_any_call(cli, monkeypatch):
    from fastapi.testclient import TestClient
    from agent8088 import web_server
    monkeypatch.setattr(web_server, "_eng", lambda: cli.A)
    monkeypatch.setattr(web_server, "_cl", lambda: cli)
    monkeypatch.setattr(cli.A, "LAST_MODEL_SERVED", {})
    served = TestClient(web_server.app).get("/api/status").json()["model_served"]
    assert served == {"provider": "", "model": "", "fallback_for": "", "reason": ""}
