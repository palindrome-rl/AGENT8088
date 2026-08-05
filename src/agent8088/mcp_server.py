"""Agent8088 MCP Server — expose curated built-in tools to external AI agents.

Starts an MCP server that lets any MCP client (Claude Code, Codex, Cursor,
etc.) use Agent8088's safe built-in tools: read files, write files, web
search, calculate, and more.

Dangerous tools (execute_shell, run_sandboxed, git_push, etc.) are NOT
exposed — the host agent's own approval surface handles those.

Usage:
    agent8088 --mcp-serve              # stdio (local, default)
    agent8088 --mcp-serve --mcp-http   # HTTP (remote, multi-client)

MCP client config (stdio):
    {
        "mcpServers": {
            "agent8088": {
                "command": "agent8088",
                "args": ["--mcp-serve"]
            }
        }
    }

MCP client config (HTTP):
    {
        "mcpServers": {
            "agent8088": {
                "url": "http://localhost:8931/mcp"
            }
        }
    }
"""
import inspect
import sys
import logging

logger = logging.getLogger("agent8088.mcp_server")

try:
    from mcp.server.fastmcp import FastMCP
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

# Curated subset of built-in tools that are safe to expose over MCP.
# Following Hermes/OpenClaw pattern: only expose tools that don't require
# live agent-loop context or dangerous capabilities.
EXPOSED_TOOLS = (
    "read_text",
    "write_file",
    "calculate",
    "web_search",
    "web_search_tavily",
    "web_search_exa",
    "get_page_title",
    "last_output",
)


def create_mcp_server(host=None, port=None):
    """Build the FastMCP server with curated Agent8088 tools.

    When host and port are provided, the server is configured for HTTP
    (Streamable HTTP) transport. Otherwise, stdio is assumed.
    """
    if not _MCP_AVAILABLE:
        raise ImportError(
            "MCP server requires the 'mcp' package. "
            f"Install with: {sys.executable} -m pip install 'mcp'"
        )

    from agent8088 import engine as A

    kwargs = {
        "name": "agent8088",
        "instructions": (
            "Agent8088 local tool server. Provides file read/write, "
            "web search, and calculation tools. Write_file respects "
            "allowed_paths and blocked_paths from Agent8088 config."
        ),
    }
    if host:
        kwargs["host"] = host
        kwargs["port"] = port or 8931
        kwargs["streamable_http_path"] = "/mcp"

    mcp = FastMCP(**kwargs)

    for tool_name in EXPOSED_TOOLS:
        spec = A.TOOL_SPECS.get(tool_name)
        if not spec:
            continue

        description = spec.get("description", f"Agent8088 tool: {tool_name}")
        args_list = spec.get("args", [])

        handler = _make_handler(tool_name, args_list, A)
        handler.__name__ = tool_name
        handler.__doc__ = description

        mcp.add_tool(handler, name=tool_name, description=description)

    return mcp


def _make_handler(tool_name: str, args_list: list, engine_module):
    """Create an async handler that dispatches to engine.run_tool.

    Synthesizes a proper inspect.Signature from the tool's args list so
    FastMCP can generate correct MCP input schemas. Without this, **kwargs
    produces an empty schema and external clients can't pass arguments.
    """
    params = []
    annotations = {"return": str}
    for arg in args_list:
        params.append(inspect.Parameter(
            arg, inspect.Parameter.KEYWORD_ONLY, annotation=str
        ))
        annotations[arg] = str

    async def handler(**kwargs):
        args = {k: v for k, v in kwargs.items() if v is not None}
        result = engine_module.run_tool(tool_name, args)
        if result is None:
            return ""
        return str(result)

    handler.__name__ = tool_name
    handler.__signature__ = inspect.Signature(params)
    handler.__annotations__ = annotations
    return handler


def run_mcp_server(transport="stdio", host="127.0.0.1", port=8931):
    """Start the Agent8088 MCP server.

    transport: "stdio" (default, local) or "streamable-http" (remote/multi-client).
    host/port: only used when transport is "streamable-http".
    """
    if not _MCP_AVAILABLE:
        print(
            "Error: MCP server requires the 'mcp' package.\n"
            f"Install with: {sys.executable} -m pip install 'mcp'",
            file=sys.stderr,
        )
        sys.exit(1)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    from agent8088 import engine as A
    # MCP server exposes only safe tools (no shell, no git push).
    # Run in full-auto so safe tools (web_search, get_page_title) don't
    # escalate — the host agent's own approval handles dangerous actions.
    A.PERMISSION_MODE = "full-auto"
    # Allow loopback for configured search endpoints (SearXNG on localhost).
    if A.APP_CONFIG.get("search_base_url", ""):
        import urllib.parse as _up
        parsed = _up.urlparse(A.APP_CONFIG["search_base_url"])
        if parsed.hostname:
            A.SSRF_ALLOW_HOSTS.add(parsed.hostname)
            A.SSRF_ALLOW_HOSTS.add(f"{parsed.hostname}:{parsed.port or 80}")

    if transport == "streamable-http":
        server = create_mcp_server(host=host, port=port)
        print(f"Agent8088 MCP server (HTTP) on http://{host}:{port}/mcp", file=sys.stderr)
        server.run(transport="streamable-http")
    else:
        server = create_mcp_server()
        server.run(transport="stdio")


if __name__ == "__main__":
    run_mcp_server()