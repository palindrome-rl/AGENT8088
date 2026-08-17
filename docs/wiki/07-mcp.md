# MCP (Model Context Protocol)

[← Wiki index](README.md)

Agent8088 speaks MCP in **both** directions:

- **Client** — connect external MCP servers and use their tools as if built in
- **Server** — expose Agent8088's own safe tools to Claude Code, Codex, Cursor…

---

## Client mode

### Configuration

MCP servers are declared in `mcp.json`, using the standard `mcpServers` shape:

| Path | Scope |
|---|---|
| `$AGENT8088_HOME/mcp.json` (normally `~/.agent8088/mcp.json`) | user-level, all projects |
| `.agent8088/mcp.json` | project root — **overrides** user-level per server name |

```json
{
  "mcpServers": {
    "docs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/docs"],
      "env": { "SOME_VAR": "value" },
      "tools": { "include": ["read_*"], "exclude": ["delete_*"] }
    },
    "remote": {
      "url": "https://mcp.example.com/mcp",
      "bearer_token_env": "REMOTE_MCP_TOKEN",
      "headers": { "X-Trace": "1" },
      "timeout": 30
    }
  }
}
```

| Field | Notes |
|---|---|
| `command` + `args` | stdio transport |
| `url` | Streamable HTTP transport |
| `enabled: false` | Keep the entry, skip connecting |
| `env` | Extra env for stdio (string→string only) |
| `cwd` | Working directory for stdio |
| `tools.include` | Glob allowlist. If present, only matches register |
| `tools.exclude` | Glob denylist, applied when `include` is absent |
| `bearer_token_env` | Name of the env var holding a bearer token |
| `timeout` / `connect_timeout` | Per-call / per-connect seconds |

Exactly one of `command` or `url` is required — setting both, or neither, is a
config error reported per server.

### Managing servers from the REPL

```
/mcp                                    # status + discovered tools
/mcp reload                             # reconnect after editing config
/mcp add docs stdio npx -y @scope/server [--project]
/mcp add remote http https://mcp.example.com/mcp [--project]
/mcp remove docs [--project]
```

Written config files get mode `0600`.

### How external tools appear

Registered as `mcp_<server>_<tool>`, sanitised to lowercase
alphanumerics/underscores. Collisions get a numeric suffix
(`mcp_s_t`, `mcp_s_t_2`, …) and built-in tool names are reserved, so an MCP
server cannot shadow `write_file`.

The server's JSON Schema becomes the tool's parameter schema, so required args
are enforced the same as for built-ins.

### Security properties

These are the parts worth knowing before you connect a third-party server:

- **Responses are untrusted.** Every MCP result is wrapped in
  `<<<EXTERNAL_UNTRUSTED_CONTENT>>>` with chat-template tokens stripped, so a
  malicious server can't forge a system turn.
- **The permission layer still applies.** A tool without the server's
  `readOnlyHint` annotation needs normal one-shot approval in readonly mode.
- **stdio processes get a minimal environment.** Only `HOME`, `PATH`, `LANG`,
  `TMPDIR` and a few others are forwarded, plus whatever you list in `env`.
  Your other secrets are not inherited.
- **One bad server doesn't break the rest.** A server that fails to start is
  recorded as `error` in `/mcp` status; the others still connect.

---

## Circuit breaker

Three consecutive failures from one server open a breaker for 60 seconds. While it
is open, calls to that server's tools return an error that tells the model **not**
to retry yet and how long is left; a success resets it. Breakers are per server, so
one dead server does not silence a healthy one.

Without this the model retried a dead server every round and spent the whole
request on something that was not coming back. `/mcp` still shows the real
connection state.

## Server mode

Expose Agent8088's tools to another agent.

```sh
agent8088 --mcp-serve                # stdio (local)
agent8088 --mcp-http --mcp-port 8931 # Streamable HTTP — --mcp-http/--mcp-port/--mcp-host imply --mcp-serve
```

Client config:

```json
{ "mcpServers": { "agent8088": { "command": "agent8088", "args": ["--mcp-serve"] } } }
```
```json
{ "mcpServers": { "agent8088": { "url": "http://localhost:8931/mcp" } } }
```

### What's exposed

Read-only by default — 6 tools (`EXPOSED_TOOLS` in `src/agent8088/mcp_server.py`):

| Tool | |
|---|---|
| `read_text` | read a file |
| `calculate` | evaluate an expression |
| `web_search` | SearXNG search |
| `get_page_title` | fetch a page title |
| `last_output` | previous tool output |
| `describe_capabilities` | what this server can do, and its active limits and guardrails |

`describe_capabilities` lets a host agent ask what this server actually offers
instead of hardcoding assumptions about it. It reads only Agent8088's own
in-memory tool and limit tables — no file, socket, or process — and its output is
redacted like any other result. A test enforces that every tool in the default
surface has a non-mutating mode, so adding one here has to be justified.

Never exposed, in any configuration: `execute_shell`, `run_sandboxed`, all
mutating git tools, `schedule_task`, `browse_page`, `spawn_subagent`,
`execute_plan`, `present_plan`.

### Why writes are opt-in

**MCP has no approval channel.** In readonly mode a blocked write returns
an `ESCALATION_REQUEST` payload, which is just a string the client can't answer — there is
no prompt to show anyone. So the server runs the engine in **full-auto**, and
that is only safe while the exposed set is non-mutating. A test asserts every
default-exposed tool has a non-mutating mode, so a future addition can't quietly
lose its prompt.

To expose `write_file`:

```ini
mcp_server_allow_writes=1
```

The server logs a warning at startup when this is on. **Narrow `allowed_paths`
and set `blocked_paths` first** — writes are unattended.

Even with writes enabled, the always-on floor holds: credential files and shell
startup files (`.zshrc`, `.bashrc`, `.profile`, …) are refused. SSRF checks also
still apply, so an MCP client can't use `get_page_title` to probe your metadata
endpoint.

### HTTP transport caveat

Binds to `127.0.0.1` by default and is restricted to localhost. Remote MCP
needs an authenticated proxy that is not included in Agent8088.

---

## Both directions at once

Nothing stops you connecting servers *and* serving. A useful pattern:
`browse_page` dropped in favour of a Playwright MCP server, while exposing
Agent8088's search tools to your editor's agent.

To drop it, comment out its line in `tools.txt` — there is no `disabled_tools`
config key, and setting one has no effect. See
[Disabling a built-in](04-tools.md#disabling-a-built-in).
