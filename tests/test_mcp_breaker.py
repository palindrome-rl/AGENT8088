"""Per-MCP-server circuit breaker.

Hermes (tools/mcp_tool.py): 3 consecutive failures open a breaker for a 60s
cooldown, and while it is open the tool returns an error that explicitly tells the
model not to retry yet. A success resets it.

Without this, a dead MCP server is retried on every call — the model burns its
whole turn budget on a server that is not coming back inside this request, and the
error text gives it no reason to stop.
"""
import pytest

from agent8088.mcp import MCPRuntime


@pytest.fixture
def runtime(tmp_path):
    rt = MCPRuntime(tmp_path)
    rt._tools = {"mcp_x_do": {"mcp_server": "x", "mcp_tool": "do", "timeout": 5}}
    return rt


def _always_fail(rt, monkeypatch):
    def _boom(coroutine, timeout=35):
        # The runtime wraps the coroutine; close it so asyncio doesn't warn.
        try:
            coroutine.close()
        except Exception:
            pass
        raise RuntimeError("connection refused")

    monkeypatch.setattr(rt, "_run", _boom)


def _always_ok(rt, monkeypatch):
    def _fine(coroutine, timeout=35):
        try:
            coroutine.close()
        except Exception:
            pass
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(rt, "_run", _fine)


def test_failures_are_reported_before_the_breaker_opens(runtime, monkeypatch):
    _always_fail(runtime, monkeypatch)
    for _ in range(runtime.BREAKER_THRESHOLD):
        out = runtime.call("mcp_x_do", {})
        assert "failed" in out.lower()
        assert "do not retry" not in out.lower()


def test_breaker_opens_after_the_threshold(runtime, monkeypatch):
    _always_fail(runtime, monkeypatch)
    for _ in range(runtime.BREAKER_THRESHOLD):
        runtime.call("mcp_x_do", {})
    out = runtime.call("mcp_x_do", {})
    assert "do not retry" in out.lower()
    assert "consecutive" in out.lower()


def test_open_breaker_does_not_call_the_server(runtime, monkeypatch):
    _always_fail(runtime, monkeypatch)
    for _ in range(runtime.BREAKER_THRESHOLD):
        runtime.call("mcp_x_do", {})

    calls = {"n": 0}

    def _count(coroutine, timeout=35):
        calls["n"] += 1
        try:
            coroutine.close()
        except Exception:
            pass
        raise RuntimeError("connection refused")

    monkeypatch.setattr(runtime, "_run", _count)
    runtime.call("mcp_x_do", {})
    assert calls["n"] == 0


def test_breaker_message_names_the_remaining_cooldown(runtime, monkeypatch):
    _always_fail(runtime, monkeypatch)
    for _ in range(runtime.BREAKER_THRESHOLD):
        runtime.call("mcp_x_do", {})
    assert "s" in runtime.call("mcp_x_do", {})  # "~60s"


def test_breaker_closes_after_the_cooldown(runtime, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(runtime, "_now", lambda: clock["t"])
    _always_fail(runtime, monkeypatch)
    for _ in range(runtime.BREAKER_THRESHOLD):
        runtime.call("mcp_x_do", {})
    assert "do not retry" in runtime.call("mcp_x_do", {}).lower()
    clock["t"] += runtime.BREAKER_COOLDOWN_SEC + 1
    out = runtime.call("mcp_x_do", {})
    assert "do not retry" not in out.lower()   # tried again, failed normally


def test_success_resets_the_failure_count(runtime, monkeypatch):
    _always_fail(runtime, monkeypatch)
    for _ in range(runtime.BREAKER_THRESHOLD - 1):
        runtime.call("mcp_x_do", {})
    _always_ok(runtime, monkeypatch)
    runtime.call("mcp_x_do", {})
    _always_fail(runtime, monkeypatch)
    # Count was reset, so one more failure must not open the breaker.
    assert "do not retry" not in runtime.call("mcp_x_do", {}).lower()


def test_breakers_are_per_server(runtime, monkeypatch):
    """One dead server must not silence a healthy one."""
    runtime._tools["mcp_y_do"] = {"mcp_server": "y", "mcp_tool": "do", "timeout": 5}
    _always_fail(runtime, monkeypatch)
    for _ in range(runtime.BREAKER_THRESHOLD):
        runtime.call("mcp_x_do", {})
    assert "do not retry" in runtime.call("mcp_x_do", {}).lower()
    assert "do not retry" not in runtime.call("mcp_y_do", {}).lower()


def test_unknown_tool_still_reports_plainly(runtime):
    assert "not available" in runtime.call("mcp_nope", {})


def test_threshold_zero_disables_the_breaker(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "BREAKER_THRESHOLD", 0)
    _always_fail(runtime, monkeypatch)
    for _ in range(20):
        assert "do not retry" not in runtime.call("mcp_x_do", {}).lower()
