# src/agent8088/web_server.py
"""Thin FastAPI bridge wrapping the Agent8088 engine for the optional web UI.

Launched via `agent8088 --web`. Does NOT reimplement agent logic — it calls
the same run_agent(), run_tool(), and cmd_* functions the CLI uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

log = logging.getLogger("agent8088.web")

# --- Engine imports (lazy, to avoid import-order issues) ---
_engine = None
_cli = None

def _eng():
    global _engine
    if _engine is None:
        from agent8088 import engine as _e
        _engine = _e
    return _engine

def _cl():
    global _cli
    if _cli is None:
        from agent8088 import cli as _c
        _cli = _c
    return _cli


# --- Lifespan: initialize engine once ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    A = _eng()
    # Same initialization the CLI does in main() before starting the REPL
    A.resolve_auto_search_provider()
    A.verify_sandbox_backend()
    print(f"Agent8088 web server ready", flush=True)
    yield
    print("Agent8088 web server shutting down", flush=True)


app = FastAPI(title="Agent8088 Web Bridge", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5180", "http://localhost:5180"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === REST endpoints ===

@app.get("/api/status")
async def get_status():
    """Current session/engine status."""
    A, C = _eng(), _cl()
    S = C.S
    return {
        "model": A.MODEL_NAME,
        "provider": C._active_provider_name(),
        "context_pct": C._estimate_context_pct(),
        "permission_mode": A.PERMISSION_MODE,
        "session_name": S.name or "",
        "last_usage": S.last_usage,
        "verbose": S.verbose,
        "usage_mode": S.usage_mode,
        "show_trace": S.show_trace,
        "show_reasoning": S.show_reasoning,
        "temperature": S.temperature,
        "max_turns": S.max_turns,
        "disabled_skills": sorted(S.disabled_skills),
    }


@app.get("/api/tools")
async def get_tools():
    """Full tool registry: all 32 tools with args, mode, description."""
    A = _eng()
    tools = []
    for name, spec in sorted(A.TOOL_SPECS.items()):
        tools.append({
            "name": name,
            "description": spec.get("description") or A.default_tool_description(name),
            "mode": spec.get("mode", ""),
            "args": spec.get("args", []),
            "optional": spec.get("optional", []),
            "arg_types": spec.get("arg_types", {}),
            "path_arg": spec.get("path_arg", ""),
            "timeout": spec.get("timeout", 25),
            "aliases": [],
            "enabled": name in C._active_tool_specs() if (C := _cl()) else True,
        })
    return tools


@app.post("/api/tool/{name}")
async def invoke_tool(name: str, body: dict = None):
    """Execute a single tool directly (parity with /tool <name> <args>)."""
    A = _eng()
    args = body or {}
    result = A.run_tool(name, args)
    return {"name": name, "result": result}


@app.get("/api/skills")
async def get_skills():
    """Skills list with metadata (lazy body load on expand)."""
    A = _eng()
    skills = []
    for name, pkg in sorted(A.SKILL_PACKAGES.items()):
        resources = pkg.get("resources", [])
        skills.append({
            "name": name,
            "description": pkg.get("description", ""),
            "resources": [r for r in resources],
            "enabled": name not in _cl().S.disabled_skills,
        })
    return skills


@app.get("/api/skills/{name}/resource/{resource}")
async def get_skill_resource(name: str, resource: str):
    """Load one skill resource (SKILL.md or a reference file)."""
    A = _eng()
    content = A.read_skill_resource(name, resource)
    return {"name": name, "resource": resource, "content": content}


@app.post("/api/skills/{name}/toggle")
async def toggle_skill(name: str, body: dict = None):
    """Enable/disable a skill for the current session."""
    C = _cl()
    enable = (body or {}).get("enable", True)
    if enable:
        C.S.disabled_skills.discard(name)
    else:
        C.S.disabled_skills.add(name)
    C._save_preferences()
    return {"name": name, "enabled": name not in C.S.disabled_skills}


@app.get("/api/agents")
async def get_agents():
    """Sub-agent profiles."""
    A = _eng()
    agents = []
    for name, spec in sorted(A.SUBAGENT_SPECS.items()):
        agents.append({
            "name": name,
            "description": spec.get("description", ""),
            "tools": spec.get("tools", []),
            "max_turns": spec.get("max_turns", 8),
            "permission": spec.get("permission", ""),
        })
    return agents


@app.post("/api/agent/{name}")
async def run_agent(name: str, body: dict = None):
    """Launch a sub-agent (parity with /agent <name> <task>)."""
    A = _eng()
    task = (body or {}).get("task", "")
    result = A.run_tool("spawn_subagent", {"agent_type": name, "task": task})
    return {"agent": name, "result": result}


@app.get("/api/capabilities")
async def get_capabilities():
    """Full self-report."""
    A = _eng()
    return {"report": A.describe_capabilities()}


@app.get("/api/config")
async def get_config():
    """Active configuration."""
    A = _eng()
    return {
        "model_name": A.MODEL_NAME,
        "model_base_url": A.MODEL_BASE_URL,
        "default_provider": A.DEFAULT_PROVIDER,
        "active_provider": _cl()._active_provider_name() if _cl() else "",
        "config_path": str(A.CONFIG_PATH),
        "context_window": A.CONTEXT_WINDOW,
        "tools_file": str(A.TOOLS_FILE),
        "system_file": str(A.SYSTEM_FILE),
        "skills_dir": str(A.SKILLS_DIR),
        "agents_dir": str(A.AGENTS_DIR),
        "project_root": str(A.PROJECT_ROOT),
        "artifacts_root": str(A.ARTIFACTS_ROOT),
        "shell_cwd": str(A.SHELL_CWD),
        "providers": {k: {kk: vv for kk, vv in v.items() if kk != "api_key"}
                      for k, v in A.PROVIDERS.items()},
    }


@app.get("/api/providers")
async def get_providers():
    """List all configured and built-in providers."""
    A = _eng()
    from agent8088.providers import BUILTIN_PROVIDERS, FALLBACK_MODELS
    return {
        "configured": list(A.PROVIDERS.keys()),
        "builtins": list(BUILTIN_PROVIDERS.keys()),
        "active": _cl()._active_provider_name() if _cl() else "",
        "details": {k: {"label": v.get("label", k), "base_url": v.get("base_url", ""),
                        "default_model": v.get("default_model", ""), "api_key_env": v.get("api_key_env", "")}
                    for k, v in BUILTIN_PROVIDERS.items()},
    }


class ModelSwitchBody(BaseModel):
    provider: str = ""
    model: str = ""

@app.post("/api/model/switch")
async def switch_model(body: ModelSwitchBody):
    """Switch active provider/model (parity with /model)."""
    A = _eng()
    try:
        client, model_name = A.activate_model(body.provider, body.model)
        return {"ok": True, "provider": body.provider or A.DEFAULT_PROVIDER, "model": model_name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/models/{provider}")
async def list_models(provider: str):
    """Fetch available models from a provider."""
    A = _eng()
    from agent8088.providers import list_models as _list_models, FALLBACK_MODELS
    try:
        client, _ = A.get_client(provider)
        models = _list_models(provider, client)
    except Exception:
        models = FALLBACK_MODELS.get(provider, [])
    return {"provider": provider, "models": models}


@app.get("/api/sessions")
async def list_sessions():
    """List all named sessions."""
    C = _cl()
    sessions = []
    if C.SESSIONS_DIR.exists():
        for path in sorted(C.SESSIONS_DIR.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append({
                    "name": path.stem,
                    "message_count": len(data.get("messages", [])),
                    "updated": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(path.stat().st_mtime)),
                    "active": path.stem == C.S.name,
                })
            except (OSError, json.JSONDecodeError):
                continue
    return sessions


@app.get("/api/sessions/{name}")
async def get_session(name: str):
    """Load a named session."""
    C = _cl()
    try:
        safe_name = C._session_name(name)
    except ValueError as exc:
        return {"error": str(exc)}
    path = C._session_path(safe_name)
    if not path.exists():
        return {"error": f"session not found: {name}"}
    return json.loads(path.read_text(encoding="utf-8"))


class SessionActionBody(BaseModel):
    name: str = ""
    keep: int = 6

@app.post("/api/sessions/new")
async def new_session(body: SessionActionBody):
    """Create a new named session."""
    C = _cl()
    try:
        safe_name = C._session_name(body.name)
    except ValueError as exc:
        return {"error": str(exc)}
    path = C._session_path(safe_name)
    if path.exists():
        return {"error": f"session exists: {safe_name} (use /resume)"}
    C._save_active_session()
    C.S.messages.clear()
    C.S.last_trace = None
    C.S.last_usage = None
    C.S.name = safe_name
    C._save_active_session()
    return {"ok": True, "name": safe_name}


@app.post("/api/sessions/resume")
async def resume_session(body: SessionActionBody):
    """Resume a named session."""
    C = _cl()
    try:
        safe_name = C._session_name(body.name)
    except ValueError as exc:
        return {"error": str(exc)}
    path = C._session_path(safe_name)
    if not path.exists():
        return {"error": f"session not found: {safe_name}"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}
    C._save_active_session()
    messages = data.get("messages", [])
    C.S.messages[:] = messages
    C.S.name = safe_name
    C.S.temperature = float(data.get("temperature", 0.1))
    C.S.max_turns = int(data.get("max_turns", 10))
    C.S.show_trace = bool(data.get("show_trace", False))
    C.S.show_reasoning = bool(data.get("show_reasoning", False))
    C.S.disabled_skills = set(data.get("disabled_skills", []))
    C.S.verbose = data.get("verbose", "on")
    C.S.usage_mode = data.get("usage_mode", "tokens")
    return {"ok": True, "name": safe_name, "messages": len(messages)}


@app.post("/api/sessions/reset")
async def reset_session():
    """Clear active session, retain name."""
    C = _cl()
    C.S.messages.clear()
    C.S.last_trace = None
    C.S.conversation_trace.clear()
    C.S.trace_path = ""
    C.S.last_usage = None
    C._save_active_session()
    return {"ok": True}


@app.post("/api/sessions/compact")
async def compact_session(body: SessionActionBody):
    """Compact conversation — summarize older turns."""
    C = _cl()
    keep = body.keep or 6
    # The CLI's cmd_compact calls A.run_agent with a summarization prompt.
    # We delegate to the same logic.
    if len(C.S.messages) <= keep * 2:
        return {"ok": True, "message": "Nothing to compact"}
    older = C.S.messages[:-keep] if keep > 0 else C.S.messages
    recent = C.S.messages[-keep:] if keep > 0 else []
    transcript = "\n".join(
        f"{m['role']}: {m['content'][:500]}" for m in older
        if isinstance(m.get("content"), str)
    )
    summary_prompt = (
        "Summarize the following conversation in 2-3 sentences, preserving key decisions, "
        "file paths, and code snippets:\n\n" + transcript
    )
    A = _eng()
    try:
        summary = A.run_agent(
            [{"role": "user", "content": summary_prompt}],
            max_turns=1, temperature=0.0,
        )
        C.S.messages[:] = [{"role": "assistant", "content": f"[Compacted summary]\n{summary}"}] + recent
        C._save_active_session()
        return {"ok": True, "summary": summary}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/memory/search")
async def memory_search(q: str):
    """Search persistent memory."""
    A = _eng()
    from agent8088.memory import recall
    results = recall(q)
    return {"query": q, "results": results}


class MemoryAddBody(BaseModel):
    text: str

@app.post("/api/memory/add")
async def memory_add(body: MemoryAddBody):
    """Add a fact to memory."""
    from agent8088.memory import store as _mem_store
    s = _mem_store()
    if s is None:
        return {"error": "memory is not enabled"}
    try:
        s.add(body.text, user_id="owner", source="web-ui")
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}

@app.delete("/api/memory/{fact_id}")
async def memory_forget(fact_id: str):
    """Forget a memory by ID."""
    from agent8088.memory import store as _mem_store
    s = _mem_store()
    if s is None:
        return {"error": "memory is not enabled"}
    try:
        # MemoryStore.delete(memory_id) — no user_id kwarg (TypeError otherwise).
        deleted = s.delete(fact_id)
        if not deleted:
            return {"error": f"memory not found: {fact_id}"}
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/memory/status")
async def memory_status():
    """Memory system status."""
    from agent8088.memory import status as _status
    return _status()


class MemoryToggleBody(BaseModel):
    enabled: bool

@app.post("/api/memory/toggle")
async def memory_toggle(body: MemoryToggleBody):
    """Toggle memory on/off."""
    from agent8088.memory import configure as _configure, reset as _reset
    C = _cl()
    if body.enabled:
        _configure()
        C._memory_set_enabled(True)
    else:
        _reset()
        C._memory_set_enabled(False)
    return {"ok": True, "enabled": body.enabled}


@app.get("/api/mcp")
async def get_mcp():
    """MCP servers, connection state, discovered tools."""
    A = _eng()
    statuses = getattr(A.MCP_RUNTIME, "statuses", {}) or {}
    servers = []
    for name, info in sorted(statuses.items()):
        servers.append({
            "name": name,
            "state": info.get("state", "unknown"),
            "tools": info.get("tools", []),
            "error": info.get("error", ""),
        })
    return servers


@app.post("/api/mcp/reload")
async def mcp_reload():
    """Reconnect MCP servers."""
    A = _eng()
    A.reload_mcp_tools()
    return {"ok": True}


class McpAddBody(BaseModel):
    name: str
    transport: str  # "stdio" | "http"
    command: str = ""
    url: str = ""
    project: bool = False

@app.post("/api/mcp/add")
async def mcp_add(body: McpAddBody):
    """Add an MCP server."""
    C = _cl()
    # Delegate to cmd_mcp which handles the parsing and config update
    if body.transport == "stdio":
        args = f"add {body.name} stdio {body.command}"
        if body.project:
            args += " --project"
    elif body.transport == "http":
        args = f"add {body.name} http {body.url}"
        if body.project:
            args += " --project"
    else:
        return {"error": "transport must be 'stdio' or 'http'"}
    try:
        C.cmd_mcp(args)
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


class McpRemoveBody(BaseModel):
    name: str
    project: bool = False

@app.post("/api/mcp/remove")
async def mcp_remove(body: McpRemoveBody):
    """Remove an MCP server."""
    C = _cl()
    args = f"remove {body.name}"
    if body.project:
        args += " --project"
    try:
        C.cmd_mcp(args)
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/sandbox")
async def get_sandbox():
    """Sandbox configuration."""
    A = _eng()
    return A.sandbox_status()


class SandboxBody(BaseModel):
    mode: str  # "auto" | "native" | "docker" | "local" | "setup"

@app.post("/api/sandbox")
async def set_sandbox(body: SandboxBody):
    """Configure sandbox mode."""
    C = _cl()
    try:
        C.cmd_sandbox(body.mode)
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/doctor")
async def get_doctor():
    """Health check results."""
    A, C = _eng(), _cl()
    active = C._active_provider_name()
    provider = A.PROVIDERS.get(active, {})
    endpoint = provider.get("base_url") if provider else A.MODEL_BASE_URL
    key_env = provider.get("api_key_env", "")
    if key_env:
        auth = f"{key_env}: {'set' if A._provider_api_key(provider) else 'missing'}"
    elif provider.get("api_mode", "").lower() == "litellm":
        auth = "provider-managed"
    else:
        auth = "configured" if A._provider_api_key(provider) else "not required"
    sandbox = A.sandbox_status()
    return {
        "model": f"{active}:{A.MODEL_NAME}",
        "endpoint": str(endpoint or "provider-managed"),
        "reachability": C._endpoint_probe(endpoint) if endpoint else "provider-managed",
        "authentication": auth,
        "configuration": f"{A.CONFIG_PATH} ({'found' if A.CONFIG_PATH.exists() else 'missing'})",
        "sandbox": f"{sandbox['resolved']} ({sandbox['verification']})",
        "capabilities": f"{len(C._active_tool_specs())} tools, {len(C._active_skills())} skills",
        "web_search": "ok" if A.web_search._ddgs_installed() else "broken",
        "cli_anything": "ready" if A.cli_anything.status(A.CONFIG_PATH)["available"] else "available on demand",
    }


class DoctorFixBody(BaseModel):
    fix: bool = False

@app.post("/api/doctor/fix")
async def doctor_fix(body: DoctorFixBody):
    """Run --fix repair."""
    C = _cl()
    try:
        C.cmd_doctor("--fix")
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/dump")
async def get_dump():
    """Generate redacted diagnostic bundle."""
    C = _cl()
    try:
        C.cmd_dump("")
        A = _eng()
        dump_path = A._agent_data_dir() / "dump.txt"
        if dump_path.exists():
            return PlainTextResponse(dump_path.read_text(encoding="utf-8"))
        return {"error": "dump not generated"}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/history")
async def get_history():
    """Full current conversation."""
    C = _cl()
    return {"messages": C.S.messages, "conversation_trace": C.S.conversation_trace}


class PrefBody(BaseModel):
    temperature: float | None = None
    max_turns: int | None = None
    verbose: str | None = None
    usage_mode: str | None = None
    show_trace: bool | None = None
    show_reasoning: bool | None = None
    memory_notifications: str | None = None

@app.post("/api/preferences")
async def set_preferences(body: PrefBody):
    """Update session preferences (temp, maxturns, verbose, etc.)."""
    C = _cl()
    if body.temperature is not None:
        C.S.temperature = body.temperature
    if body.max_turns is not None:
        C.S.max_turns = body.max_turns
    if body.verbose is not None and body.verbose in {"on", "off", "full"}:
        C.S.verbose = body.verbose
    if body.usage_mode is not None and body.usage_mode in {"off", "tokens", "full"}:
        C.S.usage_mode = body.usage_mode
    if body.show_trace is not None:
        C.S.show_trace = body.show_trace
    if body.show_reasoning is not None:
        C.S.show_reasoning = body.show_reasoning
    if body.memory_notifications is not None and body.memory_notifications in {"off", "on", "verbose"}:
        C.S.memory_notifications = body.memory_notifications
    C._save_preferences()
    return {"ok": True}


class LimitBody(BaseModel):
    key: str
    value: str

@app.post("/api/limits")
async def set_limit(body: LimitBody):
    """Show or change a limit."""
    A, C = _eng(), _cl()
    try:
        if body.key == "max_turns":
            old, C.S.max_turns = C.S.max_turns, int(body.value)
            C._save_preferences()
            return {"ok": True, "key": "max_turns", "old": old, "new": C.S.max_turns}
        result = A.set_limit(body.key, body.value)
        return {"ok": True, **result}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/limits")
async def get_limits():
    """Show all limits."""
    A = _eng()
    return {
        "max_turns": C.S.max_turns if (C := _cl()) else 10,
        "max_turn_seconds": A.MAX_TURN_SECONDS,
        "max_turn_tokens": A.MAX_TURN_TOKENS,
        "max_turn_cost_usd": A.MAX_TURN_COST_USD,
        "max_writes_per_turn": A.MAX_WRITES_PER_TURN,
        "max_write_bytes": A.MAX_WRITE_BYTES,
        "max_tool_timeout_seconds": A.MAX_TOOL_TIMEOUT_SECONDS,
        "max_subagent_answer_chars": A.MAX_SUBAGENT_ANSWER_CHARS,
        "denial_breaker_threshold": getattr(A, "DENIAL_BREAKER_THRESHOLD", 3),
        "context_window": A.CONTEXT_WINDOW,
        "max_completion_tokens": A.MAX_COMPLETION_TOKENS,
    }


class ModeBody(BaseModel):
    mode: str  # "readonly" | "full-auto" | "plan-only"

@app.post("/api/mode")
async def set_mode(body: ModeBody):
    """Set permission mode."""
    A = _eng()
    if body.mode in {"readonly", "full-auto"}:
        A.set_permission_mode(body.mode)
        return {"ok": True, "mode": A.PERMISSION_MODE}
    return {"error": "use /plan to enter plan-only mode"}


@app.post("/api/audit")
async def toggle_audit(body: dict):
    """Toggle plan auditing."""
    C = _cl()
    enable = body.get("enable", False)
    try:
        C.cmd_audit("on" if enable else "off")
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


# === WebSocket for streaming chat ===

class _ConnectionManager:
    """Track active WebSocket connections."""
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

manager = _ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Bidirectional WebSocket for streaming agent turns, tool events, approvals."""
    await manager.connect(ws)
    A, C = _eng(), _cl()
    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")

            if msg_type == "chat":
                await _handle_chat(ws, msg, A, C)
            elif msg_type == "command":
                await _handle_command(ws, msg, C)
            elif msg_type == "interrupt":
                # Signal the EscListener — but since we run in async, we
                # use a threading.Event shared with the agent thread.
                _interrupt_event.set()
            elif msg_type == "approval":
                _pending_approval["approved"] = msg.get("approved", False)
                _pending_approval["session_scope"] = msg.get("session_scope", False)
                _pending_approval["event"].set()
            elif msg_type == "plan_approval":
                _pending_plan_approval["mode"] = msg.get("mode", "")
                _pending_plan_approval["event"].set()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        log.error("WebSocket error: %s", exc)
        manager.disconnect(ws)


# --- Shared state for interrupt + approval flows (engine runs in a thread) ---
_interrupt_event = threading.Event()
_pending_approval: dict = {"event": threading.Event(), "approved": False, "session_scope": False}
_pending_plan_approval: dict = {"event": threading.Event(), "mode": ""}


async def _handle_chat(ws: WebSocket, msg: dict, A, C):
    """Run an agent turn in a thread, stream events to the WebSocket."""
    text = msg.get("text", "")
    if not text.strip():
        return

    _interrupt_event.clear()
    S = C.S

    # Append user message
    S.messages.append({"role": "user", "content": text})

    trace = [] if S.show_trace else None
    turn_start = time.time()
    tokens_ref = [0]

    # Capture the running event loop BEFORE spawning the thread —
    # asyncio.get_event_loop() called from a worker thread crashes or
    # returns None in Python 3.10+. This is the core fix.
    loop = asyncio.get_running_loop()

    def spin(msg_str):
        elapsed = time.time() - turn_start
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "spin", "message": msg_str,
                          "elapsed": elapsed, "tokens": tokens_ref[0]}),
            loop,
        )
        from contextlib import nullcontext
        return nullcontext()

    def on_token(kind, delta):
        tokens_ref[0] += 1
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "token", "kind": kind, "delta": delta}),
            loop,
        )

    def on_calls(calls):
        call_list = [{"name": c.get("name", ""), "args": c.get("arguments", {})}
                     for c in calls] if calls else []
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "tool_calls", "calls": call_list}),
            loop,
        )

    def on_tool(name):
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "tool_start", "name": name}),
            loop,
        )

    def on_result(name, result):
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "tool_result", "name": name, "result": result[:5000]}),
            loop,
        )

    def on_escalation(name, result):
        """Approval flow — send to WebSocket, wait for response."""
        esc_id = f"esc-{int(time.time()*1000)}"
        change_type = "write"
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "escalation", "tool_name": name,
                          "change_type": change_type, "description": result[:1000],
                          "id": esc_id}),
            loop,
        )
        _pending_approval["event"].clear()
        _pending_approval["event"].wait(timeout=300)
        approved = _pending_approval.get("approved", False)
        if approved:
            A.grant_escalation()
        return approved

    def on_answer(answer):
        elapsed = time.time() - turn_start
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "answer", "text": answer,
                          "usage": {"seconds": elapsed, "tokens": tokens_ref[0],
                                    "context": C._estimate_context_pct()}}),
            loop,
        )

    # Run the agent in a thread to not block the event loop
    def _run():
        try:
            answer = A.run_agent(
                S.messages,
                max_turns=C._turn_max_turns(A.PERMISSION_MODE),
                temperature=S.temperature,
                memory_run_id=S.name or None,
                memory_background=True,
                spin=spin, on_calls=on_calls, on_tool=on_tool,
                on_result=on_result, on_escalation=on_escalation,
                on_answer=on_answer, on_token=on_token,
                interrupt_check=_interrupt_event.is_set, trace=trace,
                system_prompt=C._session_system_prompt,
                tools_def=lambda: A.build_tools_def(C._active_tool_specs()),
                allowed_tools=lambda: set(C._active_tool_specs()),
            )
            S.last_usage = {"seconds": time.time() - turn_start,
                             "tokens": tokens_ref[0],
                             "context": C._estimate_context_pct()}
            C._save_active_session()
            asyncio.run_coroutine_threadsafe(
                ws.send_json({"type": "session_saved", "name": S.name or ""}),
                loop,
            )
        except A.AgentInterrupted:
            asyncio.run_coroutine_threadsafe(
                ws.send_json({"type": "interrupted", "elapsed": time.time() - turn_start,
                              "partial": ""}),
                loop,
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            asyncio.run_coroutine_threadsafe(
                ws.send_json({"type": "error", "message": str(exc)}),
                loop,
            )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    # Await thread completion without blocking the event loop
    await loop.run_in_executor(None, thread.join)


async def _handle_command(ws: WebSocket, msg: dict, C):
    """Execute a slash command and return the result."""
    command = msg.get("command", "")
    args = msg.get("args", "")
    handler = C.COMMANDS.get(command.lower())
    if not handler:
        await ws.send_json({"type": "command_result", "command": command,
                             "result": f"unknown command: /{command}"})
        return
    try:
        # Capture console output — cmd_* functions print to Rich console.
        # For the web UI, we redirect to capture their text output.
        import io
        from rich.console import Console as RichConsole
        buf = io.StringIO()
        temp_console = RichConsole(file=buf, force_terminal=False, no_color=True, width=120)
        original_console = C.console
        C.console = temp_console
        try:
            handler(args)
        finally:
            C.console = original_console
        output = buf.getvalue()
        await ws.send_json({"type": "command_result", "command": command,
                            "result": output})
    except Exception as exc:
        await ws.send_json({"type": "command_result", "command": command,
                             "result": f"error: {exc}"})


# === Static file serving (production mode) ===

def _mount_static(app: FastAPI, dist_dir: Path):
    """Mount the built frontend for production mode (no separate dev server)."""
    from fastapi.staticfiles import StaticFiles
    if dist_dir.exists():
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")
        # SPA fallback
        from fastapi.responses import FileResponse
        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            file_path = dist_dir / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(dist_dir / "index.html")


def run_web_server(host: str = "127.0.0.1", port: int = 8180, dev: bool = False):
    """Launch the web server. Called from cli.py when --web flag is used."""
    import uvicorn
    if not dev:
        # Try to mount the built frontend
        dist_dir = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
        _mount_static(app, dist_dir)
    print(f"Agent8088 web UI on http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")