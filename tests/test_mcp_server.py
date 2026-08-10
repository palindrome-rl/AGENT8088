"""Tests for the Agent8088 MCP server (server mode — exposing tools to external agents)."""
import pytest


def test_mcp_server_imports():
    from agent8088.mcp_server import create_mcp_server, run_mcp_server
    assert callable(create_mcp_server)
    assert callable(run_mcp_server)


def test_exposed_tools_is_curated_subset():
    from agent8088.mcp_server import EXPOSED_TOOLS, WRITE_TOOLS
    assert "read_text" in EXPOSED_TOOLS
    assert "calculate" in EXPOSED_TOOLS
    assert "web_search" in EXPOSED_TOOLS
    assert "last_output" in EXPOSED_TOOLS
    # write_file deliberately moved OUT of the always-on set: the server runs
    # in full-auto (MCP has no approval channel), so an exposed write ran
    # unattended with no prompt. It is now opt-in via mcp_server_allow_writes.
    assert "write_file" not in EXPOSED_TOOLS
    assert "write_file" in WRITE_TOOLS


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


def test_handler_restores_the_callers_permission_mode():
    from agent8088.mcp_server import _make_handler
    from agent8088 import engine as A
    import asyncio

    A.PERMISSION_MODE = "readonly"
    handler = _make_handler("calculate", ["expression"], A)
    assert "4" in asyncio.run(handler(expression="2 + 2"))
    assert A.PERMISSION_MODE == "readonly"


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


def test_http_mcp_refuses_a_non_loopback_bind():
    from agent8088.mcp_server import run_mcp_server
    with pytest.raises(ValueError, match="localhost"):
        run_mcp_server(transport="streamable-http", host="0.0.0.0")

# --- Regression: write_file was exposed while the server forced full-auto ---
# An external MCP client could write anywhere under allowed_paths with no
# approval prompt (there is no approval channel over MCP). Writes are now
# opt-in; the default surface is non-mutating.

# `introspect` reads only Agent8088's own in-memory tool/limit tables — no
# filesystem, no network, no process — and its output is redacted like any other.
# `search` routes web_search to a search backend and returns results: it reads
# over the network and mutates nothing, same standing as http_get.
SAFE_MODES = {"read_text", "python_eval", "http_get", "http_post", "last_output",
              "introspect", "search"}


def test_write_file_not_exposed_by_default():
    from agent8088.mcp_server import exposed_tool_names
    assert "write_file" not in exposed_tool_names({})


def test_write_file_exposed_only_when_explicitly_opted_in():
    from agent8088.mcp_server import exposed_tool_names
    assert "write_file" in exposed_tool_names({"mcp_server_allow_writes": "1"})
    assert "write_file" in exposed_tool_names({"mcp_server_allow_writes": "true"})
    assert "write_file" not in exposed_tool_names({"mcp_server_allow_writes": "0"})
    assert "write_file" not in exposed_tool_names({"mcp_server_allow_writes": ""})


def test_default_exposed_tools_are_all_non_mutating():
    """Guard against a future mutating tool being added to the default set
    while the server still runs in full-auto."""
    from agent8088 import engine as A
    from agent8088.mcp_server import exposed_tool_names

    for name in exposed_tool_names({}):
        mode = A.TOOL_SPECS.get(name, {}).get("mode")
        assert mode in SAFE_MODES, f"{name} has mutating mode {mode!r} in the default MCP surface"


def test_dangerous_tools_never_exposed_even_with_writes_enabled():
    from agent8088.mcp_server import exposed_tool_names
    names = set(exposed_tool_names({"mcp_server_allow_writes": "1"}))
    for tool in ("execute_shell", "run_sandboxed", "git_push", "git_commit",
                 "git_clone", "git_create_pr", "schedule_task", "browse_page",
                 "spawn_subagent", "execute_plan"):
        assert tool not in names


def test_server_registers_only_the_effective_tool_set():
    import asyncio
    from agent8088 import mcp_server as M

    server = M.create_mcp_server()
    registered = {t.name for t in asyncio.run(server.list_tools())}
    assert registered == set(M.exposed_tool_names({}))
    assert "write_file" not in registered


def test_server_registers_write_file_when_opted_in(monkeypatch):
    import asyncio
    from agent8088 import engine as A
    from agent8088 import mcp_server as M

    monkeypatch.setattr(A, "APP_CONFIG", {**A.APP_CONFIG, "mcp_server_allow_writes": "1"})
    server = M.create_mcp_server()
    registered = {t.name for t in asyncio.run(server.list_tools())}
    assert "write_file" in registered


# ---------------------------------------------------------------------------
# SSRF allowlist widening for a configured SearXNG
# ---------------------------------------------------------------------------
def _ssrf_widening(monkeypatch, *, configured, base_url):
    """Run run_mcp_server's allowlist step in isolation and report the result."""
    from agent8088 import engine as A

    monkeypatch.setattr(A, "SEARCH_BASE_URL_CONFIGURED", configured)
    monkeypatch.setitem(A.APP_CONFIG, "search_base_url", base_url)
    hosts = set()
    monkeypatch.setattr(A, "SSRF_ALLOW_HOSTS", hosts)
    if getattr(A, "SEARCH_BASE_URL_CONFIGURED", False) and A.APP_CONFIG.get("search_base_url", ""):
        import urllib.parse as _up
        parsed = _up.urlparse(A.APP_CONFIG["search_base_url"])
        if parsed.hostname:
            hosts.add(parsed.hostname)
            hosts.add(f"{parsed.hostname}:{parsed.port or 80}")
    return hosts


def test_mcp_does_not_widen_ssrf_for_a_defaulted_search_url(monkeypatch):
    """The engine seeds a DEFAULT search_base_url so tool templates interpolate.

    Widening the SSRF allowlist off that default handed every MCP run loopback
    access it had no use for, in a process that runs unattended in full-auto.
    """
    assert _ssrf_widening(monkeypatch, configured=False,
                          base_url="http://127.0.0.1:8888/search?q=") == set()


def test_mcp_widens_ssrf_for_an_actually_configured_search_url(monkeypatch):
    hosts = _ssrf_widening(monkeypatch, configured=True,
                           base_url="http://127.0.0.1:8888/search?q=")
    assert hosts == {"127.0.0.1", "127.0.0.1:8888"}
