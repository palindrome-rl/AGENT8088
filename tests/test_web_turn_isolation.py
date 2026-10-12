"""Web turns are isolated per socket: one tab's Stop or closed tab cannot end
another tab's turn, two turns can't run on the shared session at once, and a
failed turn still saves. Also: "I could not answer:" answers are errors,
retry notices and stream resets reach the browser, and a busy port is a
one-line message.

Fake engine/socket, as in test_web_trace_recording.py; no model, no network.
"""
import asyncio
import socket
import threading

import pytest

from agent8088 import cli, web_server


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)

    def types(self):
        return [e.get("type") for e in self.events]


class _Engine:
    class AgentInterrupted(Exception):
        pass

    def __init__(self, artifacts_root, body):
        self.PERMISSION_MODE = "full-auto"
        self.ARTIFACTS_ROOT = artifacts_root
        self.body = body
        self.kwargs = None

    def build_tools_def(self, specs):
        return []

    def run_agent(self, messages, **kwargs):
        self.kwargs = kwargs
        return self.body(kwargs, self)

    def current_rate_limit_status(self, provider):
        return None


@pytest.fixture
def chat(monkeypatch, tmp_path):
    fresh = cli.Session()
    monkeypatch.setattr(cli, "S", fresh)
    monkeypatch.setattr(web_server, "_validated_attachments", lambda *a, **k: [])
    saves = []
    # The turn saves its own Session (not whichever chat is viewed).
    monkeypatch.setattr(web_server, "_save_session", lambda C, S: saves.append(1))

    def run(body, ws=None, text="hello"):
        engine = _Engine(tmp_path, body)
        ws = ws or _Socket()
        asyncio.run(web_server._handle_chat(ws, {"text": text}, engine, cli))
        return engine, ws

    run.saves = saves
    return run


def test_could_not_answer_is_sent_as_an_error(chat):
    def body(kwargs, engine):
        kwargs["on_answer"]("I could not answer: API key rejected by openai (HTTP 401). "
                            "Run /doctor to check your setup.")
        return "x"

    _engine, ws = chat(body)
    errors = [e for e in ws.events if e["type"] == "error"]
    assert errors and errors[0]["message"].startswith("I could not answer:")
    assert "answer" not in ws.types()


def test_status_and_stream_reset_reach_the_browser(chat):
    def body(kwargs, engine):
        kwargs["on_token"]("content", "partial ")
        kwargs["on_status"]("retrying in 2s (429)")
        kwargs["on_stream_reset"]()
        kwargs["on_token"]("content", "fresh")
        return "fresh"

    _engine, ws = chat(body)
    spins = [e["message"] for e in ws.events if e["type"] == "spin"]
    assert "retrying in 2s (429)" in spins
    types = ws.types()
    assert types.index("stream_reset") > types.index("token")
    assert [e["delta"] for e in ws.events if e["type"] == "token"][-1] == "fresh"


def test_the_interrupt_check_is_this_turns_own_event(chat):
    def body(kwargs, engine):
        return "ok"

    engine, ws = chat(body)
    check = engine.kwargs["interrupt_check"]
    assert check() is False
    # The turn has finished, so its event is no longer registered for the socket.
    assert id(ws) not in web_server._turn_interrupts


def test_interrupting_one_socket_leaves_another_turn_running():
    a, b = object(), object()
    event_a, event_b = threading.Event(), threading.Event()
    web_server._turn_interrupts[id(a)] = event_a
    web_server._turn_interrupts[id(b)] = event_b
    try:
        web_server._interrupt_turn_of(b)
        assert event_b.is_set() and not event_a.is_set()
    finally:
        web_server._turn_interrupts.pop(id(a), None)
        web_server._turn_interrupts.pop(id(b), None)


def test_a_disconnect_only_denies_that_sockets_approvals():
    mine, theirs = threading.Event(), threading.Event()
    web_server._pending_approvals["esc-a"] = {"event": mine, "approved": False, "owner": 1}
    web_server._pending_approvals["esc-b"] = {"event": theirs, "approved": False, "owner": 2}
    try:
        web_server._fail_pending_waits(owner=1)
        assert mine.is_set() and not theirs.is_set()
        assert "esc-b" in web_server._pending_approvals
    finally:
        web_server._pending_approvals.clear()


def test_a_second_turn_is_refused_while_one_runs(chat):
    assert web_server._TURN_LOCK.acquire(blocking=False)
    try:
        engine, ws = chat(lambda kwargs, engine: pytest.fail("second turn ran"))
    finally:
        web_server._TURN_LOCK.release()
    assert ws.events == [{"type": "error", "message": web_server.ANOTHER_TURN_RUNNING}]
    assert cli.S.messages == []  # the refused turn didn't touch the session


def test_the_lock_is_released_after_success_error_and_interrupt(chat):
    chat(lambda kwargs, engine: "ok")
    assert not web_server._TURN_LOCK.locked()

    def boom(kwargs, engine):
        raise RuntimeError("backend exploded")

    _engine, ws = chat(boom)
    assert not web_server._TURN_LOCK.locked()
    assert any(e["type"] == "error" and "backend exploded" in e["message"] for e in ws.events)

    def interrupted(kwargs, engine):
        raise engine.AgentInterrupted()

    chat(interrupted)
    assert not web_server._TURN_LOCK.locked()


def test_failed_and_interrupted_turns_save_the_session(chat):
    def boom(kwargs, engine):
        raise RuntimeError("x")

    chat(boom)
    assert chat.saves == [1]

    def interrupted(kwargs, engine):
        raise engine.AgentInterrupted()

    chat(interrupted)
    assert chat.saves == [1, 1]


def test_a_busy_port_is_one_actionable_line():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(RuntimeError) as excinfo:
            web_server._ensure_port_free("127.0.0.1", port)
    finally:
        holder.close()
    assert str(excinfo.value) == (f"port {port} is in use — try "
                                  f"`agent8088 --web --web-port {port + 1}`")


def test_cli_web_prints_runtime_errors_without_a_traceback(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(cli.A, "resolve_auto_search_provider", lambda: None)
    monkeypatch.setattr(cli.A, "verify_sandbox_backend", lambda: None)

    def refuse(**kwargs):
        raise RuntimeError("port 8180 is in use — try `agent8088 --web --web-port 8181`")

    monkeypatch.setattr(web_server, "run_web_server", refuse)
    monkeypatch.setattr(sys, "argv", ["agent8088", "--web"])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err.strip() == "agent8088 --web: port 8180 is in use — try `agent8088 --web --web-port 8181`"
