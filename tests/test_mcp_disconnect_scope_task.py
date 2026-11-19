"""MCP server disconnect fix: the SDK's streamable_http_client enters an anyio
task group at __aenter__ and anyio refuses __aexit__ from a different asyncio
task. The old code entered the context managers in _connect (one loop task)
and exited them in _close_all (a different task), so removing a streamable
HTTP MCP server (context7 repro) printed 'Task exception was never retrieved'
plus RuntimeError noise.

The fix runs each server's CM stack on one long-lived asyncio task — the task
that enters the stack is the task that exits it. This test drives the real
_connect/_close_all against a stub stdio transport with an anyio task group
underneath, and asserts the scope's entry and exit tasks are the same.
"""

import anyio
import asyncio

import agent8088.mcp as mcp

# Bind module refs once so the monkeypatch lines stay readable.
_mcp_module = mcp


class _GroupTransport:
    """Stand-in async CM for the SDK's transport: enters a real anyio task
    group and records which asyncio task entered and exited it. Cross-task
    exit raises, mirroring the SDK's streamable_http_client behavior."""

    def __init__(self):
        self.entered_task = None
        self.exited_task = None
        self._tg = None

    async def __aenter__(self):
        self.entered_task = asyncio.current_task()
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()
        return (None, None)

    async def __aexit__(self, *exc):
        self.exited_task = asyncio.current_task()
        if self.exited_task is not self.entered_task:
            raise RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in")
        await self._tg.__aexit__(*exc)
        return False


class _FakeSession:
    async def initialize(self):
        return None

    async def list_tools(self):
        return type("Tools", (), {"tools": []})()


class _FakeClientSession:
    def __init__(self, read_stream, write_stream):
        pass

    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, *exc):
        return False


def test_mcp_disconnect_exits_scope_on_the_task_that_entered_it(monkeypatch, tmp_path):
    """Removing an MCP server must exit its CM stack on the task that entered
    it. Pre-fix this raised RuntimeError from the anyio task scope and the
    async-generator athrow died with 'Task exception was never retrieved' —
    the user-reported noise. Post-fix the host task owns both ends."""
    runtime = mcp.MCPRuntime(tmp_path)
    monkeypatch.setattr(_mcp_module.MCPRuntime, "_load_config", lambda self: {})

    holder = {}

    import mcp.client.stdio as stdio_module

    def fake_stdio_client(params):
        holder["transport"] = _GroupTransport()
        return holder["transport"]

    monkeypatch.setattr(stdio_module, "stdio_client", fake_stdio_client)
    monkeypatch.setattr("mcp.ClientSession", _FakeClientSession)

    config = {"command": "npx", "args": ["-y", "stub"]}
    tools = runtime._run(runtime._connect("context7", config, "stdio"), timeout=10)
    assert tools == []
    transport = holder["transport"]

    # Close must not raise, and the transport must record entry==exit task.
    runtime._run(runtime._close_all(), timeout=10)
    assert transport.exited_task is transport.entered_task
    assert runtime._sessions == {}
