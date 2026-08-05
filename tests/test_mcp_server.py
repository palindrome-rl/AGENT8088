"""Tests for the Agent8088 MCP server (server mode — exposing tools to external agents)."""
import pytest


def test_mcp_server_imports():
    from agent8088.mcp_server import EXPOSED_TOOLS, create_mcp_server, run_mcp_server
    assert callable(create_mcp_server)
    assert callable(run_mcp_server)


def test_exposed_tools_is_curated_subset():
    from agent8088.mcp_server import EXPOSED_TOOLS
    assert "read_text" in EXPOSED_TOOLS
    assert "write_file" in EXPOSED_TOOLS
    assert "calculate" in EXPOSED_TOOLS
    assert "web_search" in EXPOSED_TOOLS
    assert "last_output" in EXPOSED_TOOLS


def test_dangerous_tools_not_exposed():
    from agent8088.mcp_server import EXPOSED_TOOLS
    assert "execute_shell" not in EXPOSED_TOOLS
    assert "run_sandboxed" not in EXPOSED_TOOLS
    assert "git_push" not in EXPOSED_TOOLS
    assert "git_commit" not in EXPOSED_TOOLS
    assert "spawn_subagent" not in EXPOSED_TOOLS
    assert "execute_plan" not in EXPOSED_TOOLS
    assert "schedule_task" not in EXPOSED_TOOLS
    assert "browse_page" not in EXPOSED_TOOLS


def test_create_mcp_server_registers_all_exposed_tools():
    from agent8088.mcp_server import create_mcp_server, EXPOSED_TOOLS
    try:
        server = create_mcp_server()
    except ImportError:
        pytest.skip("MCP package not installed")
    # FastMCP stores tools in _tool_manager
    manager = server._tool_manager if hasattr(server, "_tool_manager") else None
    if manager and hasattr(manager, "_tools"):
        registered = set(manager._tools.keys())
    else:
        pytest.skip("Cannot introspect FastMCP tool manager")
    for tool_name in EXPOSED_TOOLS:
        assert tool_name in registered, f"{tool_name} not registered in MCP server"


def test_handler_dispatches_to_run_tool(tmp_path, monkeypatch):
    from agent8088.mcp_server import _make_handler
    from agent8088 import engine as A

    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    handler = _make_handler("read_text", ["filename"], A)
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")
    import asyncio
    result = asyncio.run(handler(filename=str(test_file)))
    assert "hello world" in result


def test_handler_returns_string():
    from agent8088.mcp_server import _make_handler
    from agent8088 import engine as A
    handler = _make_handler("calculate", ["expression"], A)
    import asyncio
    result = asyncio.run(handler(expression="2 + 2"))
    assert isinstance(result, str)
    assert "4" in result


def test_handler_has_proper_signature():
    import inspect
    from agent8088.mcp_server import _make_handler
    from agent8088 import engine as A
    handler = _make_handler("calculate", ["expression"], A)
    sig = inspect.signature(handler)
    assert "expression" in sig.parameters
    assert sig.parameters["expression"].annotation == str


def test_handler_no_args_has_empty_signature():
    import inspect
    from agent8088.mcp_server import _make_handler
    from agent8088 import engine as A
    handler = _make_handler("last_output", [], A)
    sig = inspect.signature(handler)
    assert len(sig.parameters) == 0


def test_write_file_handler_has_two_params():
    import inspect
    from agent8088.mcp_server import _make_handler
    from agent8088 import engine as A
    handler = _make_handler("write_file", ["filename", "content"], A)
    sig = inspect.signature(handler)
    assert "filename" in sig.parameters
    assert "content" in sig.parameters
    assert len(sig.parameters) == 2


def test_run_mcp_server_accepts_http_transport():
    from agent8088.mcp_server import run_mcp_server
    import inspect
    sig = inspect.signature(run_mcp_server)
    assert "transport" in sig.parameters
    assert sig.parameters["transport"].default == "stdio"
    assert "host" in sig.parameters
    assert "port" in sig.parameters
    assert sig.parameters["port"].default == 8931


def test_create_mcp_server_accepts_host_port():
    from agent8088.mcp_server import create_mcp_server
    import inspect
    sig = inspect.signature(create_mcp_server)
    assert "host" in sig.parameters
    assert "port" in sig.parameters
    assert sig.parameters["host"].default is None
    assert sig.parameters["port"].default is None


def test_create_mcp_server_http_configures_endpoint():
    try:
        from agent8088.mcp_server import create_mcp_server
        server = create_mcp_server(host="127.0.0.1", port=9999)
        # FastMCP stores settings — verify it was created without error
        assert server is not None
    except ImportError:
        pytest.skip("MCP package not installed")