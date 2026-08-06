"""Agent8088 MCP Server — expose curated built-in tools to external AI agents.

Starts an MCP server that lets any MCP client (Claude Code, Codex, Cursor,
etc.) use Agent8088's safe built-in tools: read files, web search,
calculate, and more.

Dangerous tools (execute_shell, run_sandboxed, git_push, etc.) are NOT
exposed — the host agent's own approval surface handles those.

The default surface is read-only. File writes are opt-in, because MCP has no
approval channel: an exposed write_file would run unattended with no prompt.
Enable with `mcp_server_allow_writes=1` in config.txt, and narrow
allowed_paths / set blocked_paths first.

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
#
# Everything here is NON-MUTATING. That matters because run_mcp_server() puts
# the process in full-auto: MCP has no approval channel, so an escalation
# prompt would just be an error string the client cannot answer. Non-mutating
# tools make full-auto safe; a mutating one would silently lose its prompt.
EXPOSED_TOOLS = (
    "read_text",
    "calculate",
    "web_search",
    "web_search_tavily",
    "web_search_exa",
    "get_page_title",
    "last_output",
    # Lets a host agent ask what this server can actually do — tools, limits, and
    # guardrails — instead of hardcoding assumptions about it. Reads no files and
    # makes no requests, and the report is redacted like any other output.
    "describe_capabilities",
)

# Mutating tools, exposed only when the operator explicitly opts in with
# mcp_server_allow_writes=1. Writes over MCP are unattended — there is no
# prompt — so narrow allowed_paths / set blocked_paths before enabling this.
# The always-on floor still applies: sensitive files (.env, .ssh, keys) and
# shell startup files (.zshrc and friends) are refused regardless.
WRITE_TOOLS = ("write_file",)

_TRUE_VALUES = ("1", "true", "yes", "on")


def exposed_tool_names(config=None) -> tuple:
    """Effective MCP tool surface for this config.

    Non-mutating tools always; write tools only on explicit opt-in.
    """
    if config is None:
        from agent8088 import engine as A
        config = A.APP_CONFIG
    allow_writes = str(config.get("mcp_server_allow_writes", "")).strip().lower() in _TRUE_VALUES
    return EXPOSED_TOOLS + (WRITE_TOOLS if allow_writes else ())


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

    for tool_name in exposed_tool_names(A.APP_CONFIG):
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
    # Run in full-auto so the exposed read-only tools (web_search,
    # get_page_title) don't escalate: MCP has no approval channel, so an
    # ESCALATION_REQUEST would just be an error string the client cannot
    # answer. This is only safe because the default surface is non-mutating —
    # see EXPOSED_TOOLS. Writes are opt-in (mcp_server_allow_writes) and even
    # then the always-on floor still refuses sensitive and shell startup files.
    A.PERMISSION_MODE = "full-auto"
    if exposed_tool_names(A.APP_CONFIG) != EXPOSED_TOOLS:
        print("Agent8088 MCP server: writes ENABLED (mcp_server_allow_writes) — "
              "unattended, no approval prompt. Narrow allowed_paths/blocked_paths.",
              file=sys.stderr)
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