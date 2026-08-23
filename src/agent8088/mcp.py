"""Small synchronous facade over the official async MCP client."""
import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path


_SAFE_STDIO_ENV = ("HOME", "LANG", "LC_ALL", "PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "USERPROFILE")


class _InvalidJSONNoiseFilter(logging.Filter):
    """Hide the MCP SDK's full traceback for malformed server stdout."""

    def filter(self, record):
        return record.getMessage() != "Failed to parse JSONRPC message from server"


logging.getLogger("mcp.client.stdio").addFilter(_InvalidJSONNoiseFilter())


def _agent_home():
    configured = os.environ.get("AGENT8088_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".agent8088"


def _tool_name(server, tool, used):
    stem = re.sub(r"[^a-z0-9_]+", "_", f"mcp_{server}_{tool}".lower()).strip("_") or "mcp_tool"
    name, suffix = stem, 2
    while name in used:
        name = f"{stem}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def _matches(name, patterns):
    import fnmatch
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


class MCPRuntime:
    """Own MCP sessions for this Agent8088 process and expose normal tool specs."""

    # Per-server circuit breaker. Without it a dead server is retried on every
    # call, so the model spends its whole turn budget on something that is not
    # coming back inside this request — and a bare "failed" gives it no reason to
    # stop. 0 disables.
    BREAKER_THRESHOLD = 3
    BREAKER_COOLDOWN_SEC = 60.0

    def __init__(self, project_root):
        self.project_root = Path(project_root)
        self._loop = None
        self._thread = None
        self._sessions = {}
        self._tools = {}
        self.statuses = {}
        self._server_errors = {}      # server -> consecutive failure count
        self._breaker_opened_at = {}  # server -> monotonic time the breaker opened

    @staticmethod
    def _now():
        return time.monotonic()

    def _breaker_remaining(self, server):
        """Seconds left on an open breaker, or 0 if it is closed."""
        if not self.BREAKER_THRESHOLD:
            return 0
        if self._server_errors.get(server, 0) < self.BREAKER_THRESHOLD:
            return 0
        age = self._now() - self._breaker_opened_at.get(server, 0.0)
        if age >= self.BREAKER_COOLDOWN_SEC:
            # Cooldown elapsed — let the next call through to probe the server.
            self._server_errors[server] = 0
            self._breaker_opened_at.pop(server, None)
            return 0
        return max(1, int(self.BREAKER_COOLDOWN_SEC - age))

    def _note_failure(self, server):
        self._server_errors[server] = self._server_errors.get(server, 0) + 1
        if self.BREAKER_THRESHOLD and self._server_errors[server] >= self.BREAKER_THRESHOLD:
            self._breaker_opened_at[server] = self._now()

    def _note_success(self, server):
        self._server_errors.pop(server, None)
        self._breaker_opened_at.pop(server, None)

    @property
    def config_paths(self):
        return (_agent_home() / "mcp.json", self.project_root / ".agent8088" / "mcp.json")

    def _start_loop(self):
        if self._loop:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="agent8088-mcp")
        self._thread.start()

    def _run(self, coroutine, timeout=35):
        self._start_loop()
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout)

    def _load_config(self):
        servers = {}
        for path in self.config_paths:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                entries = payload.get("mcpServers", {})
                if not isinstance(entries, dict):
                    raise ValueError("mcpServers must be an object")
                servers.update(entries)
            except Exception as exc:
                self.statuses[f"config:{path}"] = {"state": "error", "error": str(exc), "tools": []}
        return servers

    @staticmethod
    def _validate(name, config):
        if not isinstance(config, dict):
            raise ValueError("server config must be an object")
        if config.get("enabled", True) is False:
            return None
        command, url = config.get("command"), config.get("url")
        if bool(command) == bool(url):
            raise ValueError("set exactly one of command or url")
        if command and not isinstance(config.get("args", []), list):
            raise ValueError("args must be an array")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(name)):
            raise ValueError("server name may contain only letters, numbers, dot, dash, and underscore")
        return "stdio" if command else "http"

    @staticmethod
    def _stdio_env(config):
        configured = config.get("env", {})
        if not isinstance(configured, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in configured.items()):
            raise ValueError("env must be a string-to-string object")
        env = {key: os.environ[key] for key in _SAFE_STDIO_ENV if os.environ.get(key)}
        env.update(configured)
        return env

    @staticmethod
    def _http_headers(config):
        headers = config.get("headers", {})
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValueError("headers must be a string-to-string object")
        token_var = config.get("bearer_token_env")
        if token_var:
            token = os.environ.get(str(token_var))
            if not token:
                raise ValueError(f"environment variable {token_var!r} is not set")
            headers = {**headers, "Authorization": f"Bearer {token}"}
        return headers

    async def _connect(self, name, config, transport):
        from mcp import ClientSession, StdioServerParameters
        if transport == "stdio":
            from mcp.client.stdio import stdio_client
            context = stdio_client(StdioServerParameters(
                command=config["command"], args=config.get("args", []),
                env=self._stdio_env(config), cwd=config.get("cwd"),
            ))
        else:
            import httpx
            from mcp.client.streamable_http import streamable_http_client
            client = httpx.AsyncClient(headers=self._http_headers(config), timeout=config.get("timeout", 30))
            context = streamable_http_client(config["url"], http_client=client)
        streams = await context.__aenter__()
        read_stream, write_stream = streams[0], streams[1]
        session_context = ClientSession(read_stream, write_stream)
        session = await session_context.__aenter__()
        await session.initialize()
        listed = await session.list_tools()
        self._sessions[name] = (context, session_context, session, client if transport == "http" else None)
        return listed.tools

    async def _close_all(self):
        sessions, self._sessions = self._sessions, {}
        for context, session_context, _session, client in sessions.values():
            try:
                await session_context.__aexit__(None, None, None)
                await context.__aexit__(None, None, None)
                if client:
                    await client.aclose()
            except Exception:
                pass

    def reload(self, reserved=()):
        teardown_error = ""
        if self._sessions:
            try:
                self._run(self._close_all())
            except Exception as exc:
                teardown_error = str(exc)
                logging.getLogger("agent8088.mcp").warning("MCP teardown failed: %s", exc)
        self._tools, self.statuses = {}, {}
        if teardown_error:
            self.statuses["teardown"] = {
                "state": "error", "error": f"could not close prior sessions: {teardown_error}",
                "tools": [],
            }
        used = set(reserved)
        for name, config in self._load_config().items():
            try:
                transport = self._validate(name, config)
                if transport is None:
                    self.statuses[name] = {"state": "disabled", "tools": []}
                    continue
                tools = self._run(self._connect(name, config, transport), config.get("connect_timeout", 15))
                include = config.get("tools", {}).get("include", [])
                exclude = config.get("tools", {}).get("exclude", [])
                if not isinstance(include, list) or not isinstance(exclude, list) or not all(isinstance(p, str) for p in [*include, *exclude]):
                    raise ValueError("tools.include and tools.exclude must be string arrays")
                names = []
                for tool in tools:
                    if include and not _matches(tool.name, include):
                        continue
                    if not include and _matches(tool.name, exclude):
                        continue
                    registered = _tool_name(name, tool.name, used)
                    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {"type": "object"}
                    if hasattr(schema, "model_dump"):
                        schema = schema.model_dump(by_alias=True)
                    annotations = getattr(tool, "annotations", None)
                    read_only = bool(getattr(annotations, "readOnlyHint", False) if annotations else False)
                    self._tools[registered] = {
                        "description": getattr(tool, "description", None) or f"MCP tool {tool.name} from {name}",
                        "mode": "mcp", "args": list(schema.get("required", [])), "parameters": schema,
                        "mcp_server": name, "mcp_tool": tool.name, "mcp_read_only": read_only,
                        "timeout": int(config.get("timeout", 30)),
                    }
                    names.append(registered)
                self.statuses[name] = {"state": "connected", "tools": names}
            except Exception as exc:
                self.statuses[name] = {"state": "error", "error": str(exc), "tools": []}
        return dict(self._tools)

    async def _call(self, registered, arguments):
        spec = self._tools[registered]
        session = self._sessions[spec["mcp_server"]][2]
        return await session.call_tool(spec["mcp_tool"], arguments=arguments)

    def call(self, registered, arguments):
        if registered not in self._tools:
            return "Error: MCP tool is not available; run /mcp reload."
        server = self._tools[registered]["mcp_server"]
        remaining = self._breaker_remaining(server)
        if remaining:
            # Tell the model explicitly not to retry. Left to itself it will call
            # the same dead tool every round until the turn runs out.
            return (
                f"Error: MCP server '{server}' is unreachable after "
                f"{self._server_errors.get(server, 0)} consecutive failures. "
                f"Auto-retry available in ~{remaining}s. Do NOT retry this tool "
                f"yet — use another approach, or tell the user to check the server."
            )
        try:
            result = self._run(self._call(registered, arguments), self._tools[registered].get("timeout", 30))
            data = result.model_dump(by_alias=True) if hasattr(result, "model_dump") else result
            self._note_success(server)
            return json.dumps(data, default=str, ensure_ascii=False)
        except Exception as exc:
            self._note_failure(server)
            return f"Error: MCP {server} failed: {exc}"

    def close(self):
        if self._sessions:
            try:
                self._run(self._close_all())
            except Exception as exc:
                logging.getLogger("agent8088.mcp").warning("MCP shutdown teardown failed: %s", exc)
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=1)
            self._loop.close()
            self._loop = self._thread = None

    def _write_config(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def set_server(self, name, config, project=False):
        self._validate(name, config)
        path = self.config_paths[1 if project else 0]
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        servers = payload.setdefault("mcpServers", {})
        servers[name] = config
        self._write_config(path, payload)

    def remove_server(self, name, project=False):
        path = self.config_paths[1 if project else 0]
        if not path.exists():
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        removed = payload.get("mcpServers", {}).pop(name, None) is not None
        self._write_config(path, payload)
        return removed
