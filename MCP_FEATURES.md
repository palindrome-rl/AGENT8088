# MCP in Agent8088

Agent8088 can connect to Model Context Protocol (MCP) servers and use their
tools in the same agent loop as its built-in tools. Local stdio servers and
remote Streamable HTTP servers are supported.

## What was added

- MCP client support through the official Python MCP SDK.
- Automatic server connection and tool discovery when Agent8088 starts.
- Persistent MCP sessions for the life of the Agent8088 process.
- MCP tools registered as `mcp_<server>_<tool>` to prevent collisions with
  built-in tools.
- User and project MCP configuration scopes.
- `/mcp` status, reload, add, and remove commands with normal command
  autocomplete.
- Per-server tool allow/deny filters.
- Explicit stdio environment forwarding, bearer-token environment variables
  for HTTP servers, and normal Agent8088 permission prompts for non-read-only
  MCP tools.

## Requirements

MCP support requires Python 3.10 or newer. Install or update Agent8088 with:

```bash
uv sync
```

## Configure servers

Agent8088 reads these files at startup:

| Scope | Location | Use case |
|---|---|---|
| User | `~/.agent8088/mcp.json` | Private servers available to your local projects |
| Project | `.agent8088/mcp.json` | Shared project servers, suitable for version control |

When both files define the same server name, the project definition wins.

### Local stdio server

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {
        "LOG_LEVEL": "warn"
      },
      "tools": {
        "include": ["list_directory", "read_file"]
      }
    }
  }
}
```

Only a minimal operating-system environment plus the declared `env` values is
sent to a stdio server. Do not place secrets in a project-scoped file.

### Remote Streamable HTTP server

```json
{
  "mcpServers": {
    "company": {
      "url": "https://mcp.example.com/mcp",
      "bearer_token_env": "COMPANY_MCP_TOKEN",
      "tools": {
        "exclude": ["delete_*"]
      }
    }
  }
}
```

Set `COMPANY_MCP_TOKEN` in your shell environment before starting Agent8088.
You may use `headers` for additional static HTTP headers.

## CLI commands

| Command | Purpose |
|---|---|
| `/mcp` | List configured servers, connection state, errors, and discovered tools |
| `/mcp reload` | Re-read both configuration files and reconnect servers |
| `/mcp add <name> stdio <command> [args...]` | Add a private local server |
| `/mcp add <name> http <url>` | Add a private HTTP server |
| `/mcp add ... --project` | Write the server to the project configuration instead |
| `/mcp remove <name> [--project]` | Remove a server from the selected scope |
| `/tools` | Show MCP tools alongside built-in tools |
| `/capabilities` | Server-by-server connection state and tool lists, plus everything else the agent has |

For example:

```text
/mcp add docs stdio npx -y @upstash/context7-mcp
/mcp
/tool mcp_docs_query_docs query="Agent8088"
```

## Tool permissions and safety

MCP tools run through Agent8088's existing permission layer. A server tool
declared as read-only by MCP may run in readonly mode. Other MCP tools request
the normal one-action approval before they execute. MCP responses are marked as
external, untrusted content before they are added to the agent context.

## Asking the agent which servers it has

The agent can report its own MCP surface — call the `describe_capabilities` tool,
or ask in plain language ("what MCP servers are connected?"). It lists each
server with its connection state, tool count, individual tool names, and the
error text for any server that failed to start. Same data as `/mcp`, from the
same source, so the model and the human never disagree about what is connected.

## Troubleshooting

- Run `/mcp` first. Connection errors are shown per server and do not disable
  built-in Agent8088 tools.
- After editing `mcp.json`, run `/mcp reload`.
- Ensure a stdio server command is installed and available on `PATH`.
- Ensure the bearer-token environment variable exists in the same shell that
  launches Agent8088.
- Use `/tools` to confirm the discovered name. Tool names are normalized, so
  punctuation in server or tool names becomes underscores.

## Server mode

Agent8088 also works in the other direction — exposing its own safe tools to
Codex, Claude Code, Cursor or any other MCP host:

```bash
agent8088 --mcp-serve                                # stdio
agent8088 --mcp-serve --mcp-http --mcp-port 8931     # Streamable HTTP, loopback only
```

Six non-mutating tools are exposed by default (`read_text`, `calculate`,
`web_search`, `get_page_title`, `last_output`, `describe_capabilities`).
`write_file` is added only with `mcp_server_allow_writes=1`, because MCP has no
approval channel — there is no prompt for a client to answer. `execute_shell`,
`run_sandboxed`, `browse_page`, `spawn_subagent`, `schedule_task` and the
mutating git tools are never exposed in any configuration.

Full detail, including why writes are opt-in: [MCP](docs/wiki/07-mcp.md#server-mode).

## Current scope

OAuth login flows, catalog installs, resources/prompts, and dynamic tool-change
notifications are not yet supported in either direction.
