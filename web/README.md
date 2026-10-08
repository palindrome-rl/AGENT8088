# Agent8088 Web UI

An optional TypeScript web interface for Agent8088, providing 1:1 parity with every CLI command, flag, tool, sub-agent, skill, and diagnostic function.

## Quick Start

### Production mode (serves built frontend)

```bash
# Launch from the repo root (builds the frontend if needed)
agent8088 --web
# Or: agent8088 --web --web-port 8180 --web-host 127.0.0.1
```

Open `http://127.0.0.1:8180` in your browser.

### Windows: missing native binding during build

If Vite reports `Cannot find native binding` from `@tailwindcss/oxide`, the
platform-specific optional dependency may be missing. A partial `node_modules`
directory does not mean installation succeeded. The Windows installer now
reinstalls dependencies from the committed lockfile before a frontend build,
including build tools and optional native packages even when npm user settings
omit them.

To repair an existing installation, run this in PowerShell from its `web` folder:

```powershell
npm.cmd ci --include=dev --include=optional
if ($LASTEXITCODE -eq 0) { npm.cmd run build }
```

`npm ci` replaces `node_modules`; keep the committed `package-lock.json`. Using
`npm.cmd` also avoids PowerShell execution-policy errors from `npm.ps1`.
If this still fails, collect `node --version`, `npm.cmd --version`,
`node -p "process.platform + ' ' + process.arch"`, and the first error in the
installation/build output. Missing packages, a native DLL that cannot load,
network failures, and an unsupported Node version need different fixes.

### Development mode (Vite dev server + FastAPI backend)

```bash
# Starts the FastAPI backend and Vite together
uv run agent8088 --web
```

Open `http://127.0.0.1:5180` — Vite proxies `/api` and `/ws` to the FastAPI backend.

## Architecture

```
Browser (React/Vite)  ←──WebSocket──→  FastAPI (web_server.py)  ──→  Engine (run_agent)
       ↑                                    ↑
       └────── REST /api/* ─────────────────┘
```

- **Frontend:** Vite 6 + React 18 + TypeScript (strict) + Tailwind CSS v4 + Zustand + TanStack Query
- **Backend:** FastAPI wraps the existing `run_agent()` engine — no agent logic reimplemented
- **Real-time:** WebSocket `/ws` for streaming tokens, tool events, thinking traces, approval prompts
- **Sessions:** Same `~/.agent8088/sessions/*.json` store as the CLI — sessions are interchangeable

## CLI Flag

```bash
agent8088 --web              # Launch web UI (production, serves built files)
agent8088 --web --web-port 3000   # Custom port
agent8088 --web --web-host 127.0.0.2  # Another loopback address
uv run agent8088 --web         # Dev mode (FastAPI + Vite)
```

The server binds to loopback only; a non-loopback `--web-host` is refused.
Nothing here is authenticated and `POST /api/tool/{name}` runs any tool,
`execute_shell` included. For access from another machine, forward the port:
`ssh -N -L 8180:127.0.0.1:8180 <host>`.

## 1:1 CLI Surface Mapping

| CLI Command | Web UI Page/Component | API Endpoint |
|---|---|---|
| `<text>` (free chat) | Chat Panel | `WS /ws {type:chat}` |
| `/help` | Command Palette (Cmd+K) | `WS {type:command}` |
| `/tools` | Tools Page | `GET /api/tools` |
| `/tool <name> <args>` | Tools Page invoker | `POST /api/tool/{name}` |
| `/capabilities` | Config Page dashboard | `GET /api/capabilities` |
| `/agents` | Sub-Agents Page | `GET /api/agents` |
| `/agent [name] [task]` | Sub-Agents runner | `POST /api/agent/{name}` |
| `/skills [name\|enable\|disable]` | Skills Page | `GET /api/skills`, `POST /api/skills/{name}/toggle` |
| `/cli-anything [task]` | Chat command | `WS {type:command}` |
| `/plan [task]` | Plan Steps + Approval Card | `WS {type:command}` + plan events |
| `/audit [on\|off]` | Config Page | `POST /api/audit` |
| `/image <path> [q]` | Image Upload | `WS / REST` |
| `/paste [q]` | Image Upload (paste) | `WS / REST` |
| `/raw <text>` | Raw Panel | `WS {type:command}` |
| `/model [provider[:model]]` | Config Page switcher | `POST /api/model/switch` |
| `/models [provider]` | Config Page picker | `GET /api/models/{provider}` |
| `/mcp` | MCP Page | `GET /api/mcp` |
| `/mcp reload` | MCP Page button | `POST /api/mcp/reload` |
| `/mcp add/remove` | MCP Page forms | `POST /api/mcp/add`, `/api/mcp/remove` |
| `/sandbox` | Config Page | `GET/POST /api/sandbox` |
| `/status` | Status Bar | `GET /api/status` |
| `/doctor [--fix]` | Doctor Page | `GET /api/doctor`, `POST /api/doctor/fix` |
| `/dump` | Doctor Page download | `GET /api/dump` |
| `/new <name>` | Sessions Page | `POST /api/sessions/new` |
| `/sessions` | Sessions Page | `GET /api/sessions` |
| `/resume <name>` | Sessions Page | `POST /api/sessions/resume` |
| `/reset` | Sessions Page | `POST /api/sessions/reset` |
| `/compact [keep]` | Sessions Page | `POST /api/sessions/compact` |
| `/limits [key value]` | Config Page | `GET/POST /api/limits` |
| `/memory [on\|off\|search\|add\|forget\|notify\|test\|clear]` | Memory Page | `GET/POST /api/memory/*` |
| `/config` | Config Page | `GET /api/config` |
| `/history` | Chat Panel | `GET /api/history` |
| `/trace [on\|off]` | Config Page | `POST /api/preferences` |
| `/think` `/reasoning [on\|off]` | Config Page | `POST /api/preferences` |
| `/verbose [on\|off\|full]` | Config Page | `POST /api/preferences` |
| `/usage [off\|tokens\|full]` | Config Page | `POST /api/preferences` |
| `/temp <float>` | Config Page | `POST /api/preferences` |
| `/maxturns <int>` | Config Page | `POST /api/preferences` |
| `/save <file>` | Chat export | Client-side download |
| `/clear` | Chat reset | `POST /api/sessions/reset` |
| `/exit` `/quit` | Session end | WS close |
| **Gateway** `/new` | Sessions Page | `POST /api/sessions/new` |
| **Gateway** `/stop` | Prompt Bar interrupt | `WS {type:interrupt}` |
| **Gateway** `/approve` | Approval Card | `WS {type:approval}` |
| **Gateway** `/deny` | Approval Card | `WS {type:approval}` |
| **Gateway** `/mode` | Config Page | `POST /api/mode` |

## Testing

```bash
# Install Playwright browsers
npm run test:install

# Run E2E tests (starts the backend automatically)
npm run test
```

## WebSocket Protocol

### Client → Server

| Type | Fields | Description |
|---|---|---|
| `chat` | `text` | Send a user message, stream the response |
| `command` | `command`, `args` | Execute a slash command |
| `interrupt` | — | Cancel the running turn |
| `approval` | `approved`, `session_scope` | Respond to a pending escalation |
| `plan_approval` | `mode` | Respond to a plan proposal (`""`, `"readonly"`, `"full-auto"`) |

### Server → Client

| Type | Fields | Description |
|---|---|---|
| `status` | `data: StatusInfo` | Engine status update |
| `token` | `kind`, `delta` | Streaming token (`reasoning` or `content`) |
| `tool_start` | `name` | Tool execution started |
| `tool_result` | `name`, `result` | Tool execution completed |
| `spin` | `message`, `elapsed`, `tokens` | Status line update |
| `escalation` | `tool_name`, `change_type`, `description`, `id` | Approval required |
| `plan_approval` | `plan`, `id` | Plan proposal for approval |
| `plan_step` | `index`, `total`, `step_text`, `tool_name`, `status` | Plan step progress |
| `answer` | `text`, `usage` | Final answer |
| `interrupted` | `elapsed`, `partial` | Turn was interrupted |
| `error` | `message` | Error occurred |
| `session_saved` | `name` | Session persisted |
| `command_result` | `command`, `result` | Slash command output |

## Security

The web UI respects all 10 Agent8088 security layers — permission modes, approval flows, path zones, credential blocks, SSRF/egress controls, command allowlists, audit trail, and all turn/resource budgets. The web UI never bypasses a guardrail the CLI enforces.
