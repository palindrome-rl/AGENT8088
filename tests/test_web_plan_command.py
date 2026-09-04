"""Web-only routing for inline /plan tasks."""

import asyncio

from agent8088 import web_server


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


class _Engine:
    def __init__(self):
        self.entered_plan_mode = False

    def enter_plan_mode(self):
        self.entered_plan_mode = True


def test_inline_plan_uses_web_chat_runner(monkeypatch):
    seen = []

    async def fake_handle_chat(ws, msg, engine, cli):
        seen.append((ws, msg, engine, cli))

    monkeypatch.setattr(web_server, "_handle_chat", fake_handle_chat)
    socket, engine, cli = _Socket(), _Engine(), object()

    asyncio.run(web_server._handle_command(socket, {"command": "plan", "args": "audit the app"}, engine, cli))

    assert engine.entered_plan_mode
    assert seen == [(socket, {"text": "audit the app"}, engine, cli)]
    assert socket.events == []
