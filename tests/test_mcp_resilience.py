"""MCP failures must be fast, named, and recoverable.

- startup connects in the background, so a dead server no longer costs
  every command (--version included) its 15s connect timeout;
- a timed-out call is cancelled on the loop instead of left running;
- an error with an empty str() (TimeoutError()) still says what happened;
- a server whose host task died is reconnected on the next call.
"""
import asyncio
import concurrent.futures
import threading
import time

import pytest

from agent8088 import mcp


class _Tool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} tool"
        self.inputSchema = {"type": "object", "properties": {}}
        self.annotations = None


@pytest.fixture
def runtime(tmp_path):
    rt = mcp.MCPRuntime(tmp_path)
    yield rt
    rt.close()


def test_timed_out_call_is_cancelled_on_the_loop(runtime):
    cancelled = threading.Event()

    async def slow():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises((TimeoutError, concurrent.futures.TimeoutError)):
        runtime._run(slow(), timeout=0.1)
    assert cancelled.wait(2), "the timed-out coroutine kept running"


def test_empty_error_text_uses_the_class_name(runtime, monkeypatch):
    runtime._tools["mcp_s_t"] = {"mcp_server": "s", "mcp_tool": "t", "timeout": 1}

    def boom(self, coroutine, timeout=35):
        coroutine.close()
        raise RuntimeError()

    monkeypatch.setattr(mcp.MCPRuntime, "_host_finished", lambda self, server: False)
    monkeypatch.setattr(mcp.MCPRuntime, "_run", boom)
    assert runtime.call("mcp_s_t", {}) == "Error: MCP s failed: RuntimeError"


def test_call_timeout_says_how_long_it_waited(runtime, monkeypatch):
    runtime._tools["mcp_s_t"] = {"mcp_server": "s", "mcp_tool": "t", "timeout": 7}

    def timeout(self, coroutine, timeout=35):
        coroutine.close()
        raise TimeoutError()

    monkeypatch.setattr(mcp.MCPRuntime, "_host_finished", lambda self, server: False)
    monkeypatch.setattr(mcp.MCPRuntime, "_run", timeout)
    assert runtime.call("mcp_s_t", {}) == "Error: MCP s failed: no answer within 7s"


def test_background_reload_returns_immediately_and_reports(runtime, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(mcp.MCPRuntime, "_load_config", lambda self: {
        "dead": {"command": "nope", "connect_timeout": 1},
        "good": {"command": "fine"},
    })

    async def fake_connect(self, name, config, transport):
        if name == "dead":
            raise ConnectionRefusedError()
        await asyncio.get_running_loop().run_in_executor(None, release.wait, 2)
        return [_Tool("echo")]

    monkeypatch.setattr(mcp.MCPRuntime, "_connect", fake_connect)
    done = []
    started = time.monotonic()
    runtime.reload_in_background(reserved={"read_text"}, on_done=done.append)
    assert time.monotonic() - started < 0.5
    assert runtime.wait_ready(0.05) is False
    assert runtime.summary()["pending"] is True
    release.set()
    assert runtime.wait_ready(5) is True
    assert list(done[0]) == ["mcp_good_echo"]
    summary = runtime.summary()
    assert summary["failed"] == ["dead"] and summary["connected"] == ["good"]
    assert summary["text"] == "MCP: 1 failed (see /mcp)"
    assert runtime.statuses["dead"]["error"] == "ConnectionRefusedError"


def test_a_dead_host_task_is_reconnected_on_the_next_call(runtime, monkeypatch):
    runtime._tools["mcp_s_t"] = {"mcp_server": "s", "mcp_tool": "t", "timeout": 5}
    runtime._configs["s"] = ({"command": "x"}, "stdio")
    finished = concurrent.futures.Future()
    finished.set_result(None)  # .done() is True, like a crashed host task
    runtime._sessions["s"] = (None, [], threading.Event(), finished, None)
    reconnects = []

    class _Session:
        async def call_tool(self, tool, arguments=None):
            return {"ok": tool}

    async def fake_connect(self, name, config, transport):
        reconnects.append(name)
        stop = asyncio.Event()
        host = asyncio.get_running_loop().create_task(stop.wait())  # alive until closed
        self._sessions[name] = (_Session(), [], stop, host, None)
        return []

    monkeypatch.setattr(mcp.MCPRuntime, "_connect", fake_connect)
    assert runtime.call("mcp_s_t", {}) == '{"ok": "t"}'
    assert reconnects == ["s"]
    assert runtime.call("mcp_s_t", {}) == '{"ok": "t"}'
    assert reconnects == ["s"], "a healthy session must not be reopened"


def test_engine_registers_background_tools_before_a_turn(engine, monkeypatch):
    spec = {"description": "d", "mode": "mcp", "args": [], "parameters": {"type": "object"},
            "mcp_server": "s", "mcp_tool": "t", "mcp_read_only": True, "timeout": 5}
    with engine._MCP_PENDING_LOCK:
        engine._MCP_PENDING["tools"] = {"mcp_s_t": spec}
    monkeypatch.setattr(engine.MCP_RUNTIME, "wait_ready", lambda timeout: True)
    assert "mcp_s_t" not in engine.TOOL_NAMES
    assert engine.ensure_mcp_ready(0) is True
    assert "mcp_s_t" in engine.TOOL_NAMES
    assert any(d["function"]["name"] == "mcp_s_t" for d in engine.TOOLS_DEF)


def test_engine_import_does_not_connect(engine):
    # The conftest config has no mcp.json; either way nothing is connected
    # synchronously -- the runtime has no loop thread until something needs one.
    assert engine.MCP_RUNTIME._loop is None
