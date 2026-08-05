import json
import sys
from pathlib import Path

import mcp
from agent8088 import engine
from agent8088.mcp import MCPRuntime


def test_project_config_overrides_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr("agent8088.mcp.Path.home", lambda: tmp_path / "home")
    runtime = MCPRuntime(tmp_path / "project")
    user, project = runtime.config_paths
    user.parent.mkdir(parents=True)
    project.parent.mkdir(parents=True)
    user.write_text(json.dumps({"mcpServers": {"docs": {"command": "user"}}}))
    project.write_text(json.dumps({"mcpServers": {"docs": {"command": "project"}, "db": {"command": "db"}}}))

    assert runtime._load_config() == {"docs": {"command": "project"}, "db": {"command": "db"}}


def test_stdio_server_is_discovered_and_called(tmp_path, monkeypatch):
    monkeypatch.setattr("agent8088.mcp.Path.home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    script = tmp_path / "server.py"
    script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('test')\n"
        "@server.tool()\n"
        "def echo(message: str) -> str:\n"
        "    return message\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    config_path = project / ".agent8088" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"mcpServers": {"test": {
        "command": sys.executable, "args": [str(script)],
        "env": {"PYTHONPATH": str(Path(mcp.__file__).parent.parent)}, "tools": {"include": ["echo"]},
    }}}), encoding="utf-8")

    runtime = MCPRuntime(project)
    try:
        tools = runtime.reload()
        assert list(tools) == ["mcp_test_echo"], runtime.statuses
        assert "hello" in runtime.call("mcp_test_echo", {"message": "hello"})
        assert runtime.statuses["test"]["state"] == "connected"
    finally:
        runtime.close()


def test_mcp_tools_use_existing_permission_gate(monkeypatch):
    name = "mcp_demo_mutate"
    monkeypatch.setitem(engine.TOOL_SPECS, name, {
        "mode": "mcp", "mcp_server": "demo", "mcp_tool": "mutate",
        "mcp_read_only": False, "args": [], "timeout": 1,
    })
    monkeypatch.setattr(engine.MCP_RUNTIME, "call", lambda *_: "done")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")

    assert engine.run_tool(name, {}).startswith("ESCALATION_REQUEST:")
    engine.grant_escalation()
    assert "done" in engine.run_tool(name, {})
