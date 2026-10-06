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


_log = logging.getLogger("agent8088.mcp")


def _describe(exc):
    """str(exc), or the class name when that is empty (TimeoutError(), CancelledError())."""
    text = " ".join(str(exc).split())
    return text or type(exc).__name__


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
        self._configs = {}            # server -> (config, transport), for reconnects
        self._loop_lock = threading.Lock()
        self._background = None       # thread running a startup reload, if any

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
        # Locked: the startup reload runs on a background thread and a tool
        # call or /mcp can arrive on the main thread at the same moment.
        with self._loop_lock:
            if self._loop:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="agent8088-mcp")
            self._thread.start()

    def _run(self, coroutine, timeout=35):
        self._start_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout)
        except BaseException:
            # A timed-out call kept running on the loop thread, holding the
            # session; cancel it so a dead server can't pile up stuck calls.
            future.cancel()
            raise

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
        """Open one MCP server session.

        The MCP SDK's streamable_http_client enters an anyio task group on
        __aenter__, and anyio refuses __aexit__ from a different asyncio task
        ('Attempted to exit cancel scope in a different task than it was
        entered in'). The old flow entered the SDK's context managers here on
        a short-lived task and exited them later from _close_all on a
        different task — surfacing as 'Task exception was never retrieved'
        whenever a streamable-http server was removed (context7 repro).

        The fix is structural: one long-lived task per server owns the whole
        'enter → ready → wait → close' CM lifecycle, so entry and exit pair on
        the same asyncio task. This coroutine just spawns that task on the
        loop and hands back the discovered tools once the server is ready.
        """
        ready = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        holder = {}

        async def _host():
            """Own this server's CM stack; runs on the loop for its lifetime."""
            import contextlib
            stack = contextlib.AsyncExitStack()
            client = None
            try:
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
                    client = httpx.AsyncClient(
                        headers=self._http_headers(config),
                        timeout=config.get("timeout", 30))
                    context = streamable_http_client(config["url"], http_client=client)
                streams = await stack.enter_async_context(context)
                read_stream, write_stream = streams[0], streams[1]
                session_context = ClientSession(read_stream, write_stream)
                session = await stack.enter_async_context(session_context)
                await session.initialize()
                listed = await session.list_tools()
                holder["session"] = session
                holder["tools"] = list(listed.tools)
                holder["client"] = client
                if not ready.done():
                    ready.set_result(list(listed.tools))
                await stop.wait()
            except asyncio.CancelledError:
                # Cancel, don't set_exception: nobody awaits `ready` after a
                # timeout, and an unread exception prints "Future exception was
                # never retrieved" at exit.
                if not ready.done():
                    ready.cancel()
                raise
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
            finally:
                try:
                    await stack.aclose()  # closed on this task, so the scope pairs
                except BaseException:
                    pass
                if client is not None:
                    try:
                        await client.aclose()
                    except BaseException:
                        pass

        task = asyncio.get_running_loop().create_task(_host())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        try:
            tools = await asyncio.wait_for(asyncio.shield(ready), timeout=config.get("connect_timeout", 15))
        except BaseException:
            # Gave up (timeout, or the caller cancelled us): the host task still
            # owns a live child process. Cancel it so stdio_client terminates the
            # child -- otherwise interpreter exit waits on that orphan (a server
            # that never answers kept `agent8088 --version` alive for minutes).
            task.cancel()
            await asyncio.wait({task}, timeout=5)
            raise
        self._sessions[name] = (holder.get("session"), holder.get("tools", []), stop, task, holder.get("client"))
        return tools

    async def _close_all(self):
        sessions, self._sessions = self._sessions, {}
        for session, _tools, stop, task, client in sessions.values():
            try:
                stop.set()
                await asyncio.wait_for(task, timeout=10)
                if client is not None:
                    await client.aclose()
            except Exception as exc:  # noqa: BLE001 -- teardown is best-effort
                _log.debug("closing an MCP session failed: %s", _describe(exc))

    def reload(self, reserved=()):
        teardown_error = ""
        if self._sessions:
            try:
                self._run(self._close_all())
            except Exception as exc:
                teardown_error = str(exc)
                logging.getLogger("agent8088.mcp").warning("MCP teardown failed: %s", exc)
        self._tools, self.statuses, self._configs = {}, {}, {}
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
                self._configs[name] = (config, transport)
                self.statuses[name] = {"state": "connecting", "tools": []}
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
                error = _describe(exc)
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    error = (f"no answer within {config.get('connect_timeout', 15)}s"
                             if isinstance(config, dict) else error)
                self.statuses[name] = {"state": "error", "error": error, "tools": []}
                # info: this can run on the background startup thread while the
                # REPL owns the terminal; statuses carries it to /mcp and the banner.
                _log.info("MCP server %s failed to connect: %s", name, error)
        return dict(self._tools)

    # -- background startup ------------------------------------------------

    def has_servers(self):
        """Whether any MCP config file exists (cheap; no connection made)."""
        return any(path.exists() for path in self.config_paths)

    def reload_in_background(self, reserved=(), on_done=None):
        """Start reload() on a daemon thread and return immediately.

        Connecting is up to connect_timeout (15s) per server, and it ran at
        engine import -- so one dead server added 15s to every command,
        --version included. on_done(tools) runs on that thread when it ends.
        """
        reserved = set(reserved)

        def work():
            try:
                tools = self.reload(reserved)
            except Exception as exc:  # noqa: BLE001 -- recorded, never raised
                _log.info("MCP background reload failed: %s", _describe(exc))
                self.statuses["startup"] = {"state": "error", "error": _describe(exc), "tools": []}
                tools = {}
            if on_done is not None:
                on_done(tools)

        self._background = threading.Thread(target=work, daemon=True, name="agent8088-mcp-startup")
        self._background.start()
        return self._background

    def wait_ready(self, timeout):
        """Wait up to `timeout`s for a background reload. True once it is done."""
        thread = self._background
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def summary(self):
        """Counts for a banner or /doctor: connected / failed / connecting servers."""
        servers = {name: status for name, status in self.statuses.items()
                   if not name.startswith(("config:", "teardown", "startup"))}
        failed = sorted(name for name, status in self.statuses.items()
                        if status.get("state") == "error")
        result = {
            "connected": sorted(n for n, st in servers.items() if st.get("state") == "connected"),
            "failed": failed,
            "connecting": sorted(n for n, st in servers.items() if st.get("state") == "connecting"),
            "tools": sum(len(st.get("tools", [])) for st in servers.values()),
            "pending": bool(self._background is not None and self._background.is_alive()),
        }
        result["text"] = (f"MCP: {len(failed)} failed (see /mcp)" if failed
                          else "MCP: connecting…" if result["pending"] or result["connecting"]
                          else f"MCP: {len(result['connected'])} connected" if result["connected"]
                          else "")
        return result

    def _host_finished(self, server):
        entry = self._sessions.get(server)
        if entry is None:
            return True
        task = entry[3]
        return task is None or task.done()

    def _reconnect(self, server):
        """Reopen one server whose host task ended (crashed or was closed)."""
        config, transport = self._configs.get(server, (None, None))
        if config is None:
            return False
        stale = self._sessions.pop(server, None)
        if stale is not None:
            try:
                # asyncio.Event is not thread-safe; set it on its own loop.
                if self._loop:
                    self._loop.call_soon_threadsafe(stale[2].set)
                else:
                    stale[2].set()
            except Exception as exc:  # noqa: BLE001
                _log.debug("stopping stale MCP session %s failed: %s", server, _describe(exc))
        self._run(self._connect(server, config, transport), config.get("connect_timeout", 15))
        self.statuses.setdefault(server, {"tools": []})["state"] = "connected"
        self.statuses[server].pop("error", None)
        _log.info("MCP server %s reconnected", server)
        return True

    async def _call(self, registered, arguments):
        spec = self._tools[registered]
        session = self._sessions[spec["mcp_server"]][0]
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
            if self._host_finished(server):
                # The server's host task ended (process exited, stream died):
                # every call would fail on the dead session until /mcp reload.
                # Reopen just this server, here, when the breaker lets a probe through.
                self._reconnect(server)
            result = self._run(self._call(registered, arguments), self._tools[registered].get("timeout", 30))
            data = result.model_dump(by_alias=True) if hasattr(result, "model_dump") else result
            self._note_success(server)
            return json.dumps(data, default=str, ensure_ascii=False)
        except Exception as exc:
            self._note_failure(server)
            detail = _describe(exc)
            if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                detail = f"no answer within {self._tools[registered].get('timeout', 30)}s"
            if self._host_finished(server):
                self.statuses.setdefault(server, {"tools": []}).update(state="error", error=detail)
            return f"Error: MCP {server} failed: {detail}"

    def close(self):
        if self._sessions:
            try:
                self._run(self._close_all())
            except Exception as exc:
                _log.warning("MCP shutdown teardown failed: %s", _describe(exc))
        if self._loop:
            self._cancel_leftover_tasks()
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=1)
            self._loop.close()
            self._loop = self._thread = None

    def _cancel_leftover_tasks(self):
        """Cancel whatever still runs on the loop (a server mid-handshake, a
        stuck call) so its child process is terminated before exit."""
        async def cancel_all():
            current = asyncio.current_task()
            pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=3)
        try:
            self._run(cancel_all(), timeout=5)
        except BaseException as exc:  # noqa: BLE001 -- teardown is best-effort
            _log.debug("cancelling leftover MCP tasks failed: %s", _describe(exc))

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
