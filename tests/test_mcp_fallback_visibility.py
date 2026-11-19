"""MCP server failures reach the capabilities registry and /status shows the
failed count. No real MCP server: statuses are set directly."""

import pytest

from agent8088 import capabilities


@pytest.fixture
def mcp(engine, monkeypatch):
    runtime = engine.MCP_RUNTIME
    monkeypatch.setattr(runtime, "statuses", {})
    monkeypatch.setattr(runtime, "_background", None)
    return engine, runtime


def test_failed_server_reports_degraded(mcp):
    engine, runtime = mcp
    runtime.statuses.update({
        "github": {"state": "connected", "tools": ["a", "b"]},
        "jira": {"state": "error", "error": "no answer within 15s", "tools": []},
    })
    engine._report_mcp()
    entry = capabilities.get(capabilities.MCP)
    assert entry.state == capabilities.DEGRADED
    assert entry.active == "1/2 servers"
    assert entry.reason == "jira: no answer within 15s"
    assert entry.fix == "/mcp"
    assert "jira" in entry.impact


def test_all_failed_is_unavailable(mcp):
    engine, runtime = mcp
    runtime.statuses["jira"] = {"state": "error", "error": "boom", "tools": []}
    engine._report_mcp()
    assert capabilities.get(capabilities.MCP).state == capabilities.UNAVAILABLE


def test_reconnect_recovers_to_ok(mcp):
    engine, runtime = mcp
    runtime.statuses["jira"] = {"state": "error", "error": "boom", "tools": []}
    engine._report_mcp()
    runtime.statuses["jira"] = {"state": "connected", "tools": ["x"]}
    engine._report_mcp()
    entry = capabilities.get(capabilities.MCP)
    assert entry.state == capabilities.OK and entry.active == "1/1 servers"


def test_no_servers_reports_nothing(mcp):
    engine, _runtime = mcp
    engine._report_mcp()
    assert capabilities.get(capabilities.MCP) is None


def test_background_connect_reports(mcp, monkeypatch):
    engine, runtime = mcp
    monkeypatch.setattr(runtime, "has_servers", lambda: True)

    def _fake_bg(reserved, on_done=None):
        runtime.statuses["jira"] = {"state": "error", "error": "refused", "tools": []}
        on_done({})

    monkeypatch.setattr(runtime, "reload_in_background", _fake_bg)
    engine._start_mcp_background()
    assert capabilities.get(capabilities.MCP).state == capabilities.UNAVAILABLE


def test_mcp_tool_call_updates_state(mcp, monkeypatch):
    engine, runtime = mcp
    runtime.statuses["jira"] = {"state": "connected", "tools": ["mcp_jira_x"]}
    engine._report_mcp()

    def _call(name, args):
        runtime.statuses["jira"] = {"state": "error", "error": "process exited", "tools": []}
        return "Error: MCP jira failed: process exited"

    monkeypatch.setattr(runtime, "call", _call)
    monkeypatch.setitem(engine.TOOL_SPECS, "mcp_jira_x",
                        {"mode": "mcp", "args": [], "description": "x",
                         "mcp_server": "jira", "mcp_tool": "x", "mcp_read_only": True})
    monkeypatch.setattr(engine, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto", raising=False)
    engine.run_tool("mcp_jira_x", {})
    assert capabilities.get(capabilities.MCP).state == capabilities.UNAVAILABLE


def test_status_shows_failed_count(mcp, monkeypatch):
    import io

    from rich.console import Console

    from agent8088 import cli
    engine, runtime = mcp
    monkeypatch.setattr(cli.A, "MCP_RUNTIME", runtime)
    runtime.statuses.update({
        "github": {"state": "connected", "tools": ["a"]},
        "jira": {"state": "error", "error": "boom", "tools": []},
    })
    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, width=200))
    monkeypatch.setattr(cli.A, "sandbox_status", lambda: {
        "resolved": "native", "verification": "verified", "requested": "auto",
        "network": "blocked"})
    monkeypatch.setattr(cli.A, "_active_model_token_limits", lambda *a: (32768, 4096))
    monkeypatch.setattr(cli.A, "context_window_source", lambda *a: "config")
    cli.cmd_status("")
    out = buffer.getvalue()
    assert "1 connected" in out and "1 failed (jira)" in out
