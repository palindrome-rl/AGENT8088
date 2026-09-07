# src/agent8088/web_server.py
"""Thin FastAPI bridge wrapping the Agent8088 engine for the optional web UI.

Launched via `agent8088 --web`. Does NOT reimplement agent logic — it calls
the same run_agent(), run_tool(), and cmd_* functions the CLI uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
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


# === Tool-call markup scrubbing ===
# The engine's tool protocol rides in the CONTENT channel: the model literally
# types `<flower>FUNCTION<flower>: name <flower>ARGS<flower>: {...}` as ordinary
# output. The CLI hides it from the live view with ProseStream hold-back and
# strips it from final answers with strip_tool_json - but the web streamed raw
# deltas and rendered raw history messages, so the markup leaked into chat
# bubbles. The helpers below are the web equivalents. The engine is NOT
# modified; session history and model context stay raw.
#
# Sentinels are built from unicode escapes so this file stays ASCII-clean.

_FLOWER = "\u273f"                      # the flower sentinel char
_FUNC = _FLOWER + "FUNCTION" + _FLOWER  # FUNCTION header sentinel
_ARGS = _FLOWER + "ARGS" + _FLOWER      # ARGS header sentinel
_TC_OPEN = "\u003ctool_call\u003e"
_TC_CLOSE = "\u003c/tool_call\u003e"
_MASK_OPEN = "\u003c|mask_start|\u003e"
_MASK_CLOSE = "\u003c|mask_end|\u003e"

_FUNC_BLOCK_RE = re.compile(re.escape(_FUNC) + r".*?" + re.escape(_ARGS) + r"\s*:\s*\{.*?\}", re.DOTALL)
_BARE_BLOCK_RE = re.compile(re.escape(_FLOWER) + r"\{.*?\}" + re.escape(_FLOWER), re.DOTALL)
_THINK_RE = re.compile(re.escape(_TC_OPEN) + r".*?" + re.escape(_TC_CLOSE), re.DOTALL)
_MASK_RE = re.compile(re.escape(_MASK_OPEN) + r".*?" + re.escape(_MASK_CLOSE), re.DOTALL)
_FRAG_RE = re.compile(re.escape(_FLOWER) + r"[^" + re.escape(_FLOWER) + r"\n]*" + re.escape(_FLOWER))


def scrub_markup(text: str) -> str:
    """Remove tool-call protocol from user-visible strings (UI display only -
    session history and model context stay raw)."""
    if not text:
        return text
    text = _FUNC_BLOCK_RE.sub("", text)
    text = _BARE_BLOCK_RE.sub("", text)
    text = _THINK_RE.sub("", text)
    text = _MASK_RE.sub("", text)
    text = _FRAG_RE.sub("", text)
    return text.replace(_FLOWER, "")


class _StreamScrubber:
    """Incrementally strips tool-call protocol from streamed content deltas.

    Web equivalent of the CLI's ProseStream: hold back any suffix that could
    still grow into a sentinel, drop confirmed call blocks whole, emit clean
    prose. Handles FUNCTION/ARGS blocks (brace-matched), bare {...} wrapped
    in flowers, tool_call tags, and mask spans. Partial sentinels at the
    buffer tail are withheld until they resolve; a runaway unterminated
    block is dropped after _MAX_HOLD bytes rather than stalling the stream.
    """

    _OPENERS = (_FUNC, _ARGS, _FLOWER + "{", _TC_OPEN, _MASK_OPEN)
    _ENDS = {
        _FUNC: "brace",
        _ARGS: _FLOWER,
        _FLOWER + "{": "brace-flower",
        _TC_OPEN: _TC_CLOSE,
        _MASK_OPEN: _MASK_CLOSE,
    }
    _MAX_HOLD = 8192

    def __init__(self):
        self._buf = ""
        self._end = None  # end-marker mode while inside a dropped block
        self._depth = 0

    def feed(self, delta: str) -> str:
        self._buf += delta
        out = []
        while True:
            if self._end is not None:
                if not self._consume_block():
                    break
                continue
            starts = [(self._buf.find(op), op) for op in self._OPENERS]
            starts = [(i, op) for i, op in starts if i != -1]
            if starts:
                i, op = min(starts)
                out.append(self._buf[:i])
                self._buf = self._buf[i:]
                self._end = self._ENDS[op]
                self._depth = 0
                continue
            # No full opener: hold back any suffix that could still grow
            # into one, emit the rest.
            keep = 0
            for op in self._OPENERS:
                for k in range(min(len(op) - 1, len(self._buf)), 0, -1):
                    if self._buf.endswith(op[:k]):
                        keep = max(keep, k)
                        break
            emit = len(self._buf) - keep
            if emit > 0:
                out.append(self._buf[:emit])
                self._buf = self._buf[emit:]
            break
        if len(self._buf) > self._MAX_HOLD:
            # Runaway unterminated block - drop it, resume emitting.
            self._buf = ""
            self._end = None
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf, self._end, self._depth = self._buf, "", None, 0
        return rest

    def _consume_block(self) -> bool:
        """Try to finish dropping the current block. True = done."""
        if self._end == _TC_CLOSE or self._end == _MASK_CLOSE:
            j = self._buf.find(self._end)
            if j == -1:
                self._buf = ""  # everything buffered is inside the block
                return False
            self._buf = self._buf[j + len(self._end):]
        elif self._end == _FLOWER:
            j = self._buf.find(_FLOWER, 1)
            if j == -1:
                self._buf = ""
                return False
            self._buf = self._buf[j + 1:]
        else:  # brace-matched JSON block
            i = 0
            while i < len(self._buf):
                ch = self._buf[i]
                if ch == "{" :
                    self._depth += 1
                elif ch == "}":
                    self._depth -= 1
                    if self._depth <= 0:
                        rest = self._buf[i + 1:]
                        if self._end == "brace-flower" and rest.startswith(_FLOWER):
                            rest = rest[1:]
                        self._buf = rest
                        self._end = None
                        self._depth = 0
                        return True
                i += 1
            self._buf = ""  # consumed into the block; keep waiting
            return False
        self._end = None
        self._depth = 0
        return True


# --- Lifespan: initialize engine once ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    A = _eng()
    # Same initialization the CLI does in main() before starting the REPL
    A.resolve_auto_search_provider()
    A.verify_sandbox_backend()
    # log.info, not print — printing after uvicorn closes stdout crashes with
    # "I/O operation on closed file".
    log.info("Agent8088 web server ready")
    yield
    log.info("Agent8088 web server shutting down")


app = FastAPI(title="Agent8088 Web Bridge", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5180", "http://localhost:5180"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === REST endpoints ===

# --- Error contract ------------------------------------------------------
# Every failure leaves this bridge as a real HTTP status with an {"error": ...}
# body. The status is what generic clients, proxies and retry logic read; the
# body keeps the human-readable message the UI shows. Returning 200 for a
# failure (as this module used to) made every caller that checks response.ok
# treat errors as success - the Schedules page rendered a 500 as "No scheduled
# tasks." for exactly that reason.

def _error(message: str, status: int = 400, **extra) -> JSONResponse:
    """A failed response: real status code, {"error": message} body."""
    payload = {"error": str(message)}
    payload.update(extra)
    return JSONResponse(payload, status_code=status)


# An agent turn is bounded by max_turns; anything past this is a runaway loop,
# and a negative value silently disabled the loop entirely.
_MAX_TURNS_CEILING = 200


def _active_base_url(A) -> str:
    """Base URL of the provider actually in use, falling back to the module default."""
    active = ""
    if (C := _cl()) is not None:
        active = C._active_provider_name() or ""
    provider = A.PROVIDERS.get(active or A.DEFAULT_PROVIDER, {})
    return provider.get("base_url") or A.MODEL_BASE_URL


def _exc_error(exc: BaseException, status: int | None = None) -> JSONResponse:
    """Map an exception to a status: bad input is the caller's fault (400),
    anything else is ours (500)."""
    if status is None:
        status = 400 if isinstance(exc, (ValueError, TypeError, KeyError)) else 500
    return _error(str(exc), status)


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
        "auto_compaction": {
            "threshold_pct": A.COMPACTION_THRESHOLD_PCT,
            "keep_messages": A.COMPACTION_KEEP_MESSAGES,
        },
        "browser": {"current_host": A.browser_status()},
    }


@app.get("/api/commands")
async def get_commands():
    """The CLI command catalog that drives web autocomplete and help."""
    return [item for item in _cl().command_catalog()
            if item["name"] not in {"exit", "quit"}]


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
            "category": str(spec.get("category") or spec.get("mode") or "other"),
            "enabled": name in C._active_tool_specs() if (C := _cl()) else True,
        })
    return tools


@app.post("/api/tool/{name}")
async def invoke_tool(name: str, body: dict = None):
    """Execute a single tool directly (parity with /tool <name> <args>)."""
    A = _eng()
    args = body or {}
    if not isinstance(args, dict):
        return _error("tool arguments must be a JSON object", 400)
    # run_tool can take minutes (shell, docker, browser) — keep it off the loop.
    # It also *raises* for ordinary outcomes (missing file, denied path), which
    # used to escape as a bare non-JSON HTTP 500 and destroy the real message -
    # the CLI prints it. Surface it the same way here.
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: A.run_tool(name, args))
    except (ValueError, OSError) as exc:
        return {"name": name, "result": f"Error: {exc}", "failed": True}
    except Exception as exc:
        log.exception("tool %s crashed", name)
        return _exc_error(exc, 500)
    if str(result).startswith("ESCALATION_REQUEST"):
        approval_id = uuid.uuid4().hex
        _pending_direct_tools[approval_id] = {"name": name, "args": args,
                                              "created": time.time()}
        return {"name": name, "result": result, "approval_required": True,
                "approval_id": approval_id}
    return {"name": name, "result": result}


@app.post("/api/tool/approval/{approval_id}")
async def approve_direct_tool(approval_id: str, body: dict = None):
    """Retry one approval-gated direct tool through the engine permission layer."""
    entry = _pending_direct_tools.pop(approval_id, None)
    if entry is None or time.time() - entry["created"] > 300:
        return _error("approval request expired", 410)
    if not bool((body or {}).get("approved")):
        return {"ok": True, "cancelled": True}
    A = _eng()
    A.grant_escalation()
    result = await asyncio.get_running_loop().run_in_executor(
        None, lambda: A.run_tool(entry["name"], entry["args"]))
    return {"name": entry["name"], "result": result}


def _skill_catalog() -> list[dict]:
    """Installed skills with metadata (lazy body load on expand)."""
    A = _eng()
    return [{
        "name": name,
        "description": pkg.get("description", ""),
        "resources": list(pkg.get("resources", [])),
        "enabled": name not in _cl().S.disabled_skills,
        "category": str(pkg.get("category") or pkg.get("group") or "General"),
    } for name, pkg in sorted(A.SKILL_PACKAGES.items())]


def _configured_mcp_names() -> set[str]:
    """Names of every MCP server the runtime knows about, connected or not."""
    statuses = getattr(_eng().MCP_RUNTIME, "statuses", {}) or {}
    return set(statuses)


# Five space-separated fields: minute hour day-of-month month day-of-week.
_CRON_FIELDS = 5


def _cron_complaint(expression: str) -> str:
    """Return why `expression` is not a usable 5-field cron string, or ""."""
    fields = (expression or "").split()
    if not fields:
        return "a cron schedule is required, e.g. '0 9 * * *' for 09:00 daily"
    if len(fields) != _CRON_FIELDS:
        return (f"a cron schedule needs {_CRON_FIELDS} fields "
                f"(minute hour day month weekday); got {len(fields)}")
    for field, allowed in zip(fields, ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))):
        for part in field.split(","):
            part = part.split("/", 1)[0]
            if part == "*":
                continue
            bounds = part.split("-")
            if not all(b.isdigit() and allowed[0] <= int(b) <= allowed[1] for b in bounds):
                return (f"'{field}' is not a valid cron field; use *, a number "
                        f"{allowed[0]}-{allowed[1]}, a range, or a step")
    return ""


@app.get("/api/skills")
async def get_skills():
    """Skills list with metadata (lazy body load on expand)."""
    return _skill_catalog()


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
    if not isinstance(enable, bool):
        return _error("'enable' must be true or false", 400)
    # Without this the endpoint happily "enabled" a misspelled skill and the UI
    # confirmed an action that did nothing.
    if name not in {skill["name"] for skill in _skill_catalog()}:
        return _error(f"unknown skill: {name}", 404)
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
            "system_prompt": spec.get("system_prompt", ""),
            "model": spec.get("model", "inherit") or "inherit",
            "builtin": bool(spec.get("builtin")),
        })
    return agents


@app.post("/api/agent/{name}")
async def run_agent(name: str, body: dict = None):
    """Launch a sub-agent (parity with /agent <name> <task>)."""
    A = _eng()
    task = (body or {}).get("task", "")
    # Sub-agents run multi-turn conversations — must not block the event loop.
    result = await asyncio.get_running_loop().run_in_executor(
        None, lambda: A.run_tool("spawn_subagent", {"agent_type": name, "task": task}))
    return {"agent": name, "result": result}


@app.post("/api/agents")
async def create_agent(body: dict = None):
    """Create the same custom markdown profile as `/agents new`."""
    A = _eng()
    result = A._exec_create_subagent(body or {})
    if result.startswith("Error:"):
        return _error(result[6:].strip(), 400)
    A.SUBAGENT_SPECS = A.load_subagent_specs(A.AGENTS_DIR, A.USER_AGENTS_DIR)
    return {"ok": True, "result": result}


@app.patch("/api/agents/{name}")
async def update_agent(name: str, body: dict = None):
    """Update custom profiles with the same validated writer as the CLI."""
    A = _eng()
    A.SUBAGENT_SPECS = A.load_subagent_specs(A.AGENTS_DIR, A.USER_AGENTS_DIR)
    profile = A.SUBAGENT_SPECS.get(name)
    if profile is None:
        return _error(f"unknown agent: {name}", 404)
    if profile.get("builtin"):
        return _error(f"'{name}' is built-in and cannot be edited", 409)
    values = dict(body or {})
    if str(values.get("name", name)).strip().lower() != name:
        return _error("renaming profiles is not supported", 400)
    values["name"] = name
    if isinstance(values.get("tools"), list):
        values["tools"] = ",".join(str(item) for item in values["tools"])
    for key in ("description", "tools", "max_turns", "model"):
        values.setdefault(key, profile.get(key, ""))
    values.setdefault("prompt", profile.get("system_prompt", ""))
    result = A.write_custom_subagent(values, allow_existing=True)
    if result.startswith("Error:"):
        return _error(result[6:].strip(), 400)
    A.SUBAGENT_SPECS = A.load_subagent_specs(A.AGENTS_DIR, A.USER_AGENTS_DIR)
    return {"ok": True, "result": result}


@app.delete("/api/agents/{name}")
async def delete_agent(name: str):
    """Delete a custom profile; built-ins stay immutable."""
    A = _eng()
    A.SUBAGENT_SPECS = A.load_subagent_specs(A.AGENTS_DIR, A.USER_AGENTS_DIR)
    profile = A.SUBAGENT_SPECS.get(name)
    if profile is None:
        return _error(f"unknown agent: {name}", 404)
    if profile.get("builtin"):
        return _error(f"'{name}' is built-in and cannot be deleted", 409)
    path = (A.USER_AGENTS_DIR / f"{name}.md").resolve()
    if path.parent != A.USER_AGENTS_DIR.resolve():
        return _error("invalid agent name", 400)
    try:
        path.unlink()
    except FileNotFoundError:
        return _error(f"profile not found: {name}", 404)
    A.SUBAGENT_SPECS = A.load_subagent_specs(A.AGENTS_DIR, A.USER_AGENTS_DIR)
    return {"ok": True}


def _task_store():
    from agent8088.task_runtime import TaskStore, store_path
    A = _eng()
    return TaskStore(A.APP_CONFIG.get("task_db_path") or store_path(A.CONFIG_PATH))


def _task_view(task: dict, operations: list[dict] | None = None) -> dict:
    """Return task state without its checkpointed model messages."""
    view = {key: value for key, value in task.items() if key != "messages_json"}
    if operations is not None:
        view["operations"] = operations
    return view


def _run_durable_task(task_id: str) -> None:
    """Continue one persisted task outside FastAPI's event loop."""
    from agent8088.task_runtime import run_task
    A, C = _eng(), _cl()
    store = _task_store()
    try:
        def agent(messages, **kwargs):
            return A.run_agent(
                messages, temperature=C.S.temperature, memory_capture=False,
                system_prompt=C._session_system_prompt,
                tools_def=lambda: A.build_tools_def(C._active_tool_specs()),
                allowed_tools=lambda: set(C._active_tool_specs()), **kwargs,
            )
        run_task("", agent, store=store, workspace=A.PROJECT_ROOT, task_id=task_id,
                 max_slices=8, slice_turns=max(4, C.S.max_turns))
    except Exception:
        log.exception("durable task %s failed to start", task_id)
    finally:
        store.close()


def _start_durable_task(task_id: str) -> None:
    threading.Thread(target=_run_durable_task, args=(task_id,), daemon=True).start()


@app.get("/api/tasks")
async def list_tasks(include_cancelled: bool = False):
    store = _task_store()
    try:
        return [_task_view(task) for task in store.list(include_cancelled=include_cancelled)]
    finally:
        store.close()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    store = _task_store()
    try:
        try:
            task = store.resolve(task_id)
        except KeyError:
            return _error(f"task not found: {task_id}", 404)
        return _task_view(task, store.recent_operations(task["id"]))
    finally:
        store.close()


@app.post("/api/tasks")
async def start_task(body: dict = None):
    goal = str((body or {}).get("goal") or "").strip()
    if not goal:
        return _error("A task goal is required.", 400)
    store = _task_store()
    try:
        task_id = store.create(goal, _eng().PROJECT_ROOT, [{"role": "user", "content": goal}])
        task = store.get(task_id)
    finally:
        store.close()
    _start_durable_task(task_id)
    return _task_view(task)


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    store = _task_store()
    try:
        try:
            task = store.resolve(task_id)
        except KeyError:
            return _error(f"task not found: {task_id}", 404)
        if task["state"] == "running":
            return _error("task is already running", 409)
        if task["state"] in {"completed", "cancelled"}:
            return _error(f"task is {task['state']} and cannot resume", 409)
    finally:
        store.close()
    _start_durable_task(task["id"])
    return _task_view(task)


@app.post("/api/tasks/{task_id}/end")
async def end_task(task_id: str):
    store = _task_store()
    try:
        try:
            task = store.resolve(task_id)
        except KeyError:
            return _error(f"task not found: {task_id}", 404)
        return _task_view(store.cancel(task["id"]))
    finally:
        store.close()


@app.get("/api/fusion/config")
async def get_fusion_config():
    A = _eng()
    return {
        "panel": [item for item in str(A.APP_CONFIG.get("fusion_panel", "")).split(",") if item],
        "judge_provider": str(A.APP_CONFIG.get("fusion_judge_provider", "")),
        "judge_model": str(A.APP_CONFIG.get("fusion_judge_model", "")),
        "max_panel": int(A.APP_CONFIG.get("fusion_max_panel", "6")),
    }


@app.post("/api/fusion/config")
async def set_fusion_config(body: dict = None):
    A = _eng()
    body = body or {}
    panel = [str(item).strip() for item in body.get("panel", []) if str(item).strip()]
    try:
        max_panel = max(1, int(body.get("max_panel", 6)))
        if len(panel) > max_panel:
            return _error(f"panel has {len(panel)} members; maximum is {max_panel}", 400)
        values = {
            "fusion_panel": ",".join(panel),
            "fusion_judge_provider": str(body.get("judge_provider", "")).strip(),
            "fusion_judge_model": str(body.get("judge_model", "")).strip(),
            "fusion_max_panel": max_panel,
        }
        A.update_simple_config(A.CONFIG_PATH, values)
        A.APP_CONFIG.update({key: str(value) for key, value in values.items()})
    except (TypeError, ValueError) as exc:
        return _exc_error(exc)
    return await get_fusion_config()


@app.post("/api/fusion/run")
async def run_fusion(body: dict = None):
    from agent8088 import fusion
    A = _eng()
    body = body or {}
    query = str(body.get("query") or "").strip()
    if not query:
        return _error("A fusion question is required.", 400)
    panel_specs = [str(item).strip() for item in body.get("panel", []) if str(item).strip()]
    try:
        panel = fusion.build_explicit_panel(panel_specs) if panel_specs else fusion.discover_panel(
            int(A.APP_CONFIG.get("fusion_max_panel", "6")))
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: fusion.run_fusion(
                query, panel=panel,
                judge_provider=str(body.get("judge_provider") or "") or None,
                judge_model=str(body.get("judge_model") or "") or None,
                max_panel_size=int(A.APP_CONFIG.get("fusion_max_panel", "6")),
                member_timeout_s=float(A.APP_CONFIG.get("fusion_member_timeout_s", "60")),
                max_workers=int(A.APP_CONFIG.get("fusion_max_workers", "8")),
                max_tokens=int(A.APP_CONFIG.get("fusion_panel_max_tokens", "1200")),
                judge_max_tokens=int(A.APP_CONFIG.get("fusion_judge_max_tokens", "500")),
                use_tools=True,
            ),
        )
    except (TypeError, ValueError) as exc:
        return _exc_error(exc)
    return {
        "query": result.query,
        "results": [{
            "provider": item.member.provider, "model": item.member.model, "text": item.text,
            "input_tokens": item.input_tokens, "output_tokens": item.output_tokens,
            "elapsed_s": item.elapsed_s, "error": item.error,
        } for item in result.results],
        "winner_index": result.winner_index,
        "winner_answer": result.winner_answer,
        "verdict": result.verdict,
        "judge_error": result.judge_error,
        "judge_parsed": result.judge_parsed,
        "total_input_tokens": result.total_input_tokens,
        "total_output_tokens": result.total_output_tokens,
        "total_cost_usd": result.total_cost_usd,
    }


@app.get("/api/capabilities")
async def get_capabilities():
    """Full self-report."""
    A = _eng()
    return {"report": A.describe_capabilities()}


@app.get("/api/config")
async def get_config():
    """Active configuration."""
    A, C = _eng(), _cl()
    return {
        "model_name": A.MODEL_NAME,
        # The active provider's endpoint - NOT engine.MODEL_BASE_URL, which is a
        # module-level default that activate_model() never updates, so the
        # Config page used to show localhost:11434 while talking to something else.
        "model_base_url": _active_base_url(A),
        "default_provider": A.DEFAULT_PROVIDER,
        "active_provider": _cl()._active_provider_name() if _cl() else "",
        "config_path": str(A.CONFIG_PATH),
        "context_window": A.CONTEXT_WINDOW,
        "max_turns": C.S.max_turns,
        "temperature": C.S.temperature,
        "tools_file": str(A.TOOLS_FILE),
        "system_file": str(A.SYSTEM_FILE),
        "skills_dir": str(A.SKILLS_DIR),
        "agents_dir": str(A.AGENTS_DIR),
        "project_root": str(A.PROJECT_ROOT),
        "artifacts_root": str(A.ARTIFACTS_ROOT),
        "shell_cwd": str(A.SHELL_CWD),
        "providers": {k: {kk: vv for kk, vv in v.items() if kk != "api_key"}
                      for k, v in A.PROVIDERS.items()},
        "auto_compaction": {
            "threshold_pct": A.COMPACTION_THRESHOLD_PCT,
            "keep_messages": A.COMPACTION_KEEP_MESSAGES,
        },
        "browser": {
            "max_steps": A.BROWSER_MAX_STEPS,
            "task_timeout_seconds": A.BROWSER_TASK_TIMEOUT_SECONDS,
            "max_actions_per_step": A.BROWSER_MAX_ACTIONS_PER_STEP,
            "headless": A.BROWSER_HEADLESS,
            "screenshots": A.BROWSER_SCREENSHOTS,
            "current_host": A.browser_status(),
        },
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
        return _exc_error(exc)


class ProviderProfileBody(BaseModel):
    name: str = "custom"
    base_url: str = ""
    model: str
    api_mode: str = "openai"
    api_key_env: str = ""


@app.post("/api/providers/custom")
async def configure_custom_provider(body: ProviderProfileBody, request: Request):
    """Configure an OpenAI-compatible endpoint without receiving a secret."""
    raw = await request.json()
    if any(key.lower() in {"api_key", "key", "token", "bearer"} for key in raw):
        return _error("raw API keys are not accepted; set an environment variable instead", 400)
    try:
        profile = _eng().configure_provider_profile(
            body.name, body.base_url, body.model, body.api_mode, body.api_key_env)
        return {"ok": True, "provider": profile}
    except Exception as exc:
        return _exc_error(exc)


@app.get("/api/models/{provider}")
async def list_models(provider: str):
    """Fetch available models from a provider."""
    A = _eng()
    from agent8088.providers import (list_models as _list_models, FALLBACK_MODELS,
                                     BUILTIN_PROVIDERS)
    # get_client() falls back to the active client for an unknown name, so this
    # used to hand back one provider's models under another provider's label -
    # inviting an invalid provider/model pairing in the Config picker.
    if provider not in A.PROVIDERS and provider not in BUILTIN_PROVIDERS:
        return _error(f"unknown provider: {provider}", 404)
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
        return _exc_error(exc)
    path = C._session_path(safe_name)
    if not path.exists():
        return _error(f"session not found: {name}", 404)
    return json.loads(path.read_text(encoding="utf-8"))


class SessionActionBody(BaseModel):
    name: str = ""
    keep: int = 6

class SessionRenameBody(BaseModel):
    new_name: str

@app.patch("/api/sessions/{name}")
async def rename_session(name: str, body: SessionRenameBody):
    """Rename a saved session without changing its transcript."""
    C = _cl()
    try:
        old_name = C._session_name(name)
        new_name = C._session_name(body.new_name)
    except ValueError as exc:
        return {"error": str(exc)}
    old_path = C._session_path(old_name)
    new_path = C._session_path(new_name)
    if not old_path.exists():
        return {"error": f"session not found: {old_name}"}
    if old_name != new_name and new_path.exists():
        return {"error": f"session exists: {new_name} (use another name)"}
    if old_name == new_name:
        return {"ok": True, "name": new_name}
    active = C.S.name == old_name
    try:
        old_path.rename(new_path)
        if active:
            C.S.name = new_name
            C._save_active_session()
        else:
            data = json.loads(new_path.read_text(encoding="utf-8"))
            data["name"] = new_name
            C._write_private_text(new_path, json.dumps(data, indent=2))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"could not rename session: {exc}"}
    return {"ok": True, "name": new_name}

@app.delete("/api/sessions/{name}")
async def delete_session(name: str):
    """Delete a saved session and clear it if it is active."""
    C = _cl()
    try:
        safe_name = C._session_name(name)
    except ValueError as exc:
        return {"error": str(exc)}
    path = C._session_path(safe_name)
    if not path.exists():
        return {"error": f"session not found: {safe_name}"}
    active = C.S.name == safe_name
    try:
        path.unlink()
    except OSError as exc:
        return {"error": f"could not delete session: {exc}"}
    if active:
        C.S.messages.clear()
        C.S.last_trace = None
        C.S.conversation_trace.clear()
        C.S.trace_path = ""
        C.S.last_usage = None
        C.S.name = ""
    return {"ok": True, "name": safe_name, "active": active}

@app.post("/api/sessions/new")
async def new_session(body: SessionActionBody):
    """Create a named session, or start an unnamed draft when name is blank."""
    C = _cl()
    if not body.name.strip():
        C._save_active_session()
        C.S.messages.clear()
        C.S.last_trace = None
        C.S.last_usage = None
        C.S.name = ""
        return {"ok": True, "name": ""}
    try:
        safe_name = C._session_name(body.name)
    except ValueError as exc:
        return _exc_error(exc)
    path = C._session_path(safe_name)
    if path.exists():
        return _error(f"session exists: {safe_name} (use /resume)", 409)
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
        return _exc_error(exc)
    path = C._session_path(safe_name)
    if not path.exists():
        return _error(f"session not found: {safe_name}", 404)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _exc_error(exc)
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
        # The summarization turn is a full model call — keep it off the loop.
        summary = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: A.run_agent(
                [{"role": "user", "content": summary_prompt}],
                max_turns=1, temperature=0.0,
            ),
        )
        C.S.messages[:] = [{"role": "assistant", "content": f"[Compacted summary]\n{summary}"}] + recent
        C._save_active_session()
        return {"ok": True, "summary": summary}
    except Exception as exc:
        return _exc_error(exc)


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
        return _error("memory is not enabled", 503)
    try:
        s.add(body.text, user_id="owner", source="web-ui")
        return {"ok": True}
    except Exception as exc:
        return _exc_error(exc)

@app.delete("/api/memory/{fact_id}")
async def memory_forget(fact_id: str):
    """Forget a memory by ID."""
    from agent8088.memory import store as _mem_store
    s = _mem_store()
    if s is None:
        return _error("memory is not enabled", 503)
    try:
        # MemoryStore.delete(memory_id) — no user_id kwarg (TypeError otherwise).
        deleted = s.delete(fact_id)
        if not deleted:
            return _error(f"memory not found: {fact_id}", 404)
        return {"ok": True}
    except Exception as exc:
        return _exc_error(exc)


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


def _search_status() -> dict:
    """Structured view of the same registry the /search command uses."""
    A = _eng()
    ctx = A._search_context()
    providers = []
    for provider in A.WEB_SEARCH_REGISTRY.all():
        try:
            available = provider.is_available(ctx)
            if provider.name == "searxng" and available:
                available = A.web_search.probe_searxng(ctx)
        except Exception:
            available = False
        schema = provider.setup_schema()
        providers.append({"name": provider.name, "available": available,
                          "badge": schema.get("badge", ""), "hint": provider.setup_hint()})
    from agent8088 import searxng_provision
    return {"selected": str(A.APP_CONFIG.get("web_search_provider") or A.web_search.AUTO),
            "active_chain": A._search_chain_summary(), "providers": providers,
            "searxng": searxng_provision.status(),
            "docker_available": A._docker_available(),
            "ssrf_guidance": "Remote SearXNG hosts must pass the existing egress and SSRF allowlists."}


@app.get("/api/search")
async def get_search():
    return _search_status()


@app.post("/api/search/use")
async def use_search(body: dict = None):
    A = _eng()
    provider = str((body or {}).get("provider") or "").strip().lower()
    known = {A.web_search.AUTO, *(item.name for item in A.WEB_SEARCH_REGISTRY.all())}
    if provider not in known:
        return _error(f"unknown search provider: {provider}", 404)
    A.update_simple_config(A.CONFIG_PATH, {"web_search_provider": provider})
    A.APP_CONFIG["web_search_provider"] = provider
    return _search_status()


@app.post("/api/search/setup")
async def setup_search(body: dict = None):
    if not bool((body or {}).get("confirmed")):
        return {"confirmation_required": True,
                "message": "Provision a local SearXNG Docker container?"}
    A = _eng()
    if not A._docker_available():
        return _error("Docker is unavailable; use the configured fallback or a remote SearXNG URL.", 503)
    from agent8088 import searxng_provision
    port = int(A.APP_CONFIG.get("searxng_port", "8080"))
    result = await asyncio.get_running_loop().run_in_executor(
        None, lambda: searxng_provision.start(A._agent_data_dir(), port=port))
    if not result.get("ok"):
        return _error(result.get("detail", "could not start SearXNG"), 503)
    ready = await asyncio.get_running_loop().run_in_executor(
        None, lambda: searxng_provision.wait_ready(port=port))
    if not ready.get("ok"):
        return _error(ready.get("detail", "SearXNG did not become ready"), 503)
    base_url = result.get("base_url") or searxng_provision.base_url(port)
    A.update_simple_config(A.CONFIG_PATH, {"search_base_url": base_url,
                                            "web_search_provider": A.web_search.AUTO})
    A.APP_CONFIG.update({"search_base_url": base_url, "web_search_provider": A.web_search.AUTO})
    A.SEARCH_BASE_URL_CONFIGURED = True
    A.resolve_auto_search_provider()
    return _search_status()


@app.post("/api/search/stop")
async def stop_search(body: dict = None):
    if not bool((body or {}).get("confirmed")):
        return {"confirmation_required": True,
                "message": "Stop the local SearXNG container?"}
    from agent8088 import searxng_provision
    result = await asyncio.get_running_loop().run_in_executor(None, searxng_provision.stop)
    return _search_status() if result.get("ok") else {"error": result.get("detail", "could not stop SearXNG")}


@app.get("/api/schedules")
async def list_schedules():
    return _eng().schedule_task()


@app.post("/api/schedules")
async def change_schedule(body: dict = None):
    body = body or {}
    action = str(body.get("action") or "").lower()
    if action not in {"add", "remove"}:
        return _error("action must be add or remove", 400)
    schedule = str(body.get("schedule") or "")
    task = str(body.get("task") or "")
    if action == "add":
        # Validate BEFORE the confirmation round-trip: the page promises the
        # engine's cron validation, but "not a cron" used to reach confirmation
        # (and an empty task with it).
        if not task.strip():
            return _error("a task to run is required", 400)
        if (invalid := _cron_complaint(schedule)):
            return _error(invalid, 400)
    if not bool(body.get("confirmed")):
        return {"confirmation_required": True,
                "message": f"{action.title()} this unattended scheduled task?"}
    result = _eng().schedule_task(action, schedule, task)
    return result if result["ok"] else _error(result["detail"], 400)


@app.post("/api/mcp/reload")
async def mcp_reload(body: dict = None):
    """Reconnect MCP servers."""
    A = _eng()
    if A.MCP_RELOAD_CONFIRM and not bool((body or {}).get("confirmed")):
        return {"confirmation_required": True,
                "message": "Reloading drops the MCP tool cache and reconnects servers."}
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
        return _error("transport must be 'stdio' or 'http'", 400)
    try:
        C.cmd_mcp(args)
        return {"ok": True}
    except Exception as exc:
        return _exc_error(exc)


class McpRemoveBody(BaseModel):
    name: str
    project: bool = False

@app.post("/api/mcp/remove")
async def mcp_remove(body: McpRemoveBody):
    """Remove an MCP server."""
    C = _cl()
    # cmd_mcp prints "server was not configured in that scope" and returns
    # normally, so this used to answer {"ok": true} for a name that never existed.
    if body.name not in _configured_mcp_names():
        return _error(f"unknown MCP server: {body.name}", 404)
    args = f"remove {body.name}"
    if body.project:
        args += " --project"
    try:
        C.cmd_mcp(args)
        return {"ok": True, "name": body.name}
    except Exception as exc:
        return _exc_error(exc)


@app.get("/api/sandbox")
async def get_sandbox():
    """Sandbox configuration."""
    A = _eng()
    return A.sandbox_status()


_SANDBOX_MODES = {"auto", "native", "docker", "local", "setup"}


class SandboxBody(BaseModel):
    mode: str  # "auto" | "native" | "docker" | "local" | "setup"

@app.post("/api/sandbox")
async def set_sandbox(body: SandboxBody):
    """Configure sandbox mode."""
    C = _cl()
    # cmd_sandbox only *prints* its complaint about an unknown mode, so without
    # this guard the endpoint answered {"ok": true} for garbage input.
    if body.mode not in _SANDBOX_MODES:
        return _error(
            f"'{body.mode}' is not a sandbox mode. "
            f"Valid modes: {', '.join(sorted(_SANDBOX_MODES))}.", 400)
    try:
        C.cmd_sandbox(body.mode)
        return {"ok": True, "mode": body.mode}
    except Exception as exc:
        return _exc_error(exc)


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
        return _exc_error(exc)


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
        return _error("dump not generated", 500)
    except Exception as exc:
        return _exc_error(exc)


@app.get("/api/history")
async def get_history():
    """Full current conversation."""
    C = _cl()
    return {"messages": C.S.messages, "conversation_trace": C.S.conversation_trace}


# Browser uploads are deliberately raw request bodies rather than multipart:
# FastAPI's optional multipart parser is not part of the Agent8088 runtime.
_ATTACHMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".xlsx", ".pptx",
                          ".png", ".jpg", ".jpeg", ".gif", ".webp"}
_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024
_ATTACHMENT_MAX_COUNT = 5


def _attachment_session(C) -> str:
    name = str(C.S.name or "web-session")
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)[:80] or "web-session"


def _attachment_index(A, C) -> Path:
    directory = A.ARTIFACTS_ROOT / ".web-attachments" / _attachment_session(C)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "index.json"


def _read_attachments(A, C) -> dict:
    try:
        loaded = json.loads(_attachment_index(A, C).read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_attachments(A, C, entries: dict) -> None:
    _attachment_index(A, C).write_text(json.dumps(entries, separators=(",", ":")), encoding="utf-8")


@app.get("/api/attachments")
async def list_attachments():
    A, C = _eng(), _cl()
    return [{"id": key, **value} for key, value in _read_attachments(A, C).items()]


@app.post("/api/attachments")
async def upload_attachment(request: Request):
    """Store one session-owned upload and return only its opaque reference."""
    A, C = _eng(), _cl()
    filename = str(request.headers.get("x-filename") or "").strip()
    if not filename or filename != Path(filename).name or "\\" in filename or len(filename) > 180:
        return _error("invalid filename", 400)
    extension = Path(filename).suffix.lower()
    if extension not in _ATTACHMENT_EXTENSIONS:
        return _error("unsupported attachment type", 400)
    body = await request.body()
    if not body:
        return _error("attachment is empty", 400)
    if len(body) > _ATTACHMENT_MAX_BYTES:
        return _error("attachment exceeds the 25MB limit", 413)
    entries = _read_attachments(A, C)
    if len(entries) >= _ATTACHMENT_MAX_COUNT:
        return _error(f"at most {_ATTACHMENT_MAX_COUNT} attachments per session", 400)
    attachment_id = uuid.uuid4().hex
    target = _attachment_index(A, C).parent / f"{attachment_id}{extension}"
    target.write_bytes(body)
    entries[attachment_id] = {"name": filename, "size": len(body), "type": extension.lstrip(".")}
    _save_attachments(A, C, entries)
    return {"id": attachment_id, **entries[attachment_id]}


@app.delete("/api/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str):
    A, C = _eng(), _cl()
    entries = _read_attachments(A, C)
    metadata = entries.pop(attachment_id, None)
    if metadata is None:
        return _error("attachment not found", 404)
    for candidate in _attachment_index(A, C).parent.glob(f"{attachment_id}.*"):
        candidate.unlink(missing_ok=True)
    _save_attachments(A, C, entries)
    return {"ok": True}


def _validated_attachments(ids: Any, A, C) -> list[dict]:
    if not isinstance(ids, list) or len(ids) > _ATTACHMENT_MAX_COUNT:
        raise ValueError("invalid attachments")
    entries = _read_attachments(A, C)
    resolved = []
    for attachment_id in ids:
        if not isinstance(attachment_id, str) or not re.fullmatch(r"[a-f0-9]{32}", attachment_id):
            raise ValueError("invalid attachment reference")
        metadata = entries.get(attachment_id)
        if metadata is None:
            raise ValueError("attachment does not belong to this session")
        candidates = list(_attachment_index(A, C).parent.glob(f"{attachment_id}.*"))
        if len(candidates) != 1 or not candidates[0].is_file():
            raise ValueError("attachment is no longer available")
        resolved.append({"id": attachment_id, "name": metadata["name"],
                         "path": str(candidates[0])})
    return resolved


# === Artifacts browser ===

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
TEXT_EXTS = {".txt", ".md", ".py", ".json", ".csv", ".yaml", ".yml", ".html",
             ".css", ".js", ".ts", ".tsx", ".sh", ".log", ".xml", ".drawio", ".toml"}


def _safe_artifact_path(A, rel: str) -> Path | None:
    """Resolve rel under ARTIFACTS_ROOT; None if it escapes or hits pycache."""
    base = A.ARTIFACTS_ROOT.resolve()
    target = (base / rel).resolve() if rel else base
    if target != base and base not in target.parents:
        return None
    if any(part == "__pycache__" for part in target.parts[len(base.parts):]):
        return None
    return target


@app.get("/api/artifacts")
async def list_artifacts(rel: str = ""):
    """List one directory under artifacts/ with type info per entry."""
    A = _eng()
    target = _safe_artifact_path(A, rel)
    if target is None or not target.exists() or not target.is_dir():
        return _error(f"not found: {rel}", 404)
    base = A.ARTIFACTS_ROOT.resolve()
    items = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            rel_child = str(entry.relative_to(base))
            if entry.is_dir():
                try:
                    count = sum(1 for child in entry.glob("*")
                                if not (child.name.startswith(".") or child.name == "__pycache__"))
                except OSError:
                    count = 0
                items.append({"name": entry.name, "path": rel_child, "type": "dir",
                              "size": None, "modified": entry.stat().st_mtime})
            else:
                ext = entry.suffix.lower()
                items.append({
                    "name": entry.name, "path": rel_child,
                    "type": "image" if ext in IMAGE_EXTS else "text" if ext in TEXT_EXTS else "file",
                    "size": entry.stat().st_size,
                    "modified": entry.stat().st_mtime,
                })
    except OSError as exc:
        return _exc_error(exc)
    return {
        "root": str(base),
        "cwd": rel,
        "parent": str(Path(rel).parent) if rel else None,
        "items": items,
    }


@app.get("/api/artifacts/file")
async def get_artifact_file(rel: str):
    """Serve one artifact file (inline for images, download otherwise)."""
    from fastapi.responses import FileResponse
    A = _eng()
    target = _safe_artifact_path(A, rel)
    if target is None or not target.is_file():
        return _error(f"not found: {rel}", 404)
    media = "image/svg+xml" if target.suffix == ".svg" else None
    return FileResponse(target, filename=target.name, media_type=media)


@app.get("/api/artifacts/content")
async def get_artifact_content(rel: str):
    """Text content of a text artifact (inline preview)."""
    A = _eng()
    target = _safe_artifact_path(A, rel)
    if target is None or not target.is_file():
        return _error(f"not found: {rel}", 404)
    if target.suffix.lower() not in TEXT_EXTS:
        return _error("binary file — use /api/artifacts/file", 400)
    if target.stat().st_size > 1_000_000:
        return _error("file too large for preview (>1MB)", 413)
    try:
        return {"path": rel, "content": target.read_text(encoding="utf-8", errors="replace")}
    except (OSError, UnicodeDecodeError) as exc:
        return _exc_error(exc)


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
        if not 0.0 <= body.temperature <= 2.0:
            return _error("temperature must be between 0.0 and 2.0", 400)
        C.S.temperature = body.temperature
    if body.max_turns is not None:
        if not 1 <= body.max_turns <= _MAX_TURNS_CEILING:
            return _error(f"max_turns must be between 1 and {_MAX_TURNS_CEILING}", 400)
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
    target: str = ""

@app.post("/api/limits")
async def set_limit(body: LimitBody):
    """Show or change a limit."""
    A, C = _eng(), _cl()
    try:
        if body.key == "max_turns":
            turns = int(body.value)
            if not 1 <= turns <= _MAX_TURNS_CEILING:
                return _error(f"max_turns must be between 1 and {_MAX_TURNS_CEILING}", 400)
            old, C.S.max_turns = C.S.max_turns, turns
            C._save_preferences()
            return {"ok": True, "key": "max_turns", "old": old, "new": C.S.max_turns}
        if body.key == "provider":
            provider, key = body.target.split(":", 1)
            result = A.set_provider_limit(provider, key, body.value)
        elif body.key == "tool_timeout":
            result = A.set_tool_timeout(body.target, body.value)
        elif body.key == "subagent_turns":
            result = A.set_subagent_turns(body.target, body.value)
        else:
            result = A.set_limit(body.key, body.value)
        return {"ok": True, **result}
    except Exception as exc:
        return _exc_error(exc)


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
        "active_model": {
            "provider": A.ACTIVE_PROVIDER or A.DEFAULT_PROVIDER,
            "model": A.MODEL_NAME,
            "context_window": A._active_model_token_limits()[0],
            "max_completion_tokens": A._active_model_token_limits()[1],
        },
        "providers": {
            name: {
                "context_window": info.get("context_window", ""),
                "max_completion_tokens": info.get("max_completion_tokens", ""),
            }
            for name, info in A.PROVIDERS.items()
        },
        "tools": {name: spec.get("timeout", 25) for name, spec in A.TOOL_SPECS.items()},
        "agents": {name: spec.get("max_turns", 8) for name, spec in A.SUBAGENT_SPECS.items()},
    }


class ModeBody(BaseModel):
    mode: str  # "readonly" | "full-auto" | "plan-only"

# plan-only is reachable here (unlike the CLI's /mode) because the browser has
# no separate /plan entry point - the picker is the only door into a plan session.
_WEB_PERMISSION_MODES = {"plan-only", "readonly", "full-auto"}


@app.post("/api/mode")
async def set_mode(body: ModeBody):
    """Set permission mode."""
    A = _eng()
    if body.mode == "plan-only":
        A.enter_plan_mode()
        return {"ok": True, "mode": A.PERMISSION_MODE}
    if body.mode in {"readonly", "full-auto"}:
        A.set_permission_mode(body.mode)
        return {"ok": True, "mode": A.PERMISSION_MODE}
    return _error(
        f"'{body.mode}' is not a permission mode. "
        f"Valid modes: {', '.join(sorted(_WEB_PERMISSION_MODES))}.", 400)


@app.post("/api/audit")
async def toggle_audit(body: dict):
    """Toggle plan auditing."""
    C = _cl()
    enable = body.get("enable", False)
    if not isinstance(enable, bool):
        return _error("'enable' must be true or false", 400)
    try:
        C.cmd_audit("on" if enable else "off")
        return {"ok": True}
    except Exception as exc:
        return _exc_error(exc)


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
    # A turn runs as its own task so this loop keeps reading. Awaiting the turn
    # inline (as this used to) meant receive_json() was not called again until
    # the turn finished, so `interrupt`, `approval` and `plan_approval` sat
    # unread in the socket for the whole turn - the stop button did nothing and
    # an approval could not release the turn that was waiting on it.
    turn: asyncio.Task | None = None

    def _busy() -> bool:
        return turn is not None and not turn.done()

    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")

            if msg_type in ("chat", "command"):
                if _busy():
                    await ws.send_json({
                        "type": "error",
                        "message": "A turn is already running. Stop it first, "
                                   "then send this again.",
                    })
                    continue
                handler = _handle_chat if msg_type == "chat" else _handle_command
                turn = asyncio.create_task(_run_turn(handler, ws, msg, A, C))
            elif msg_type == "interrupt":
                # Signal the agent thread; run_agent polls _interrupt_event.is_set.
                _interrupt_event.set()
            elif msg_type == "approval":
                esc_id = msg.get("id", "")
                entry = _pending_approvals.get(esc_id)
                if entry is not None:
                    entry["approved"] = msg.get("approved", False)
                    entry["session_scope"] = msg.get("session_scope", False)
                    entry["event"].set()
            elif msg_type == "plan_approval":
                plan_id = msg.get("id", "")
                entry = _pending_plan_approvals.get(plan_id)
                if entry is not None:
                    entry["mode"] = msg.get("mode", "")
                    entry["event"].set()
    except WebSocketDisconnect:
        manager.disconnect(ws)
        _interrupt_event.set()   # let the orphaned turn wind itself down
        _fail_pending_waits()
    except Exception as exc:
        log.error("WebSocket error: %s", exc)
        manager.disconnect(ws)
        _interrupt_event.set()
        _fail_pending_waits()


async def _run_turn(handler, ws: WebSocket, msg: dict, A, C) -> None:
    """Run one chat turn or command, reporting failures on the socket."""
    try:
        await handler(ws, msg, A, C)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("turn failed")
        try:
            await ws.send_json({"type": "error", "message": scrub_markup(str(exc))})
        except Exception:
            pass


# --- Shared state for interrupt + approval flows (engine runs in a thread) ---
# Approvals are keyed by escalation id so a timed-out or superseded prompt can
# never read the verdict meant for a different escalation.
_pending_approvals: dict = {}
_pending_plan_approvals: dict = {}
_pending_direct_tools: dict = {}
_interrupt_event = threading.Event()


def _fail_pending_waits():
    """On WS disconnect, release every waiting escalation/plan prompt as denied."""
    for entry in _pending_approvals.values():
        entry.setdefault("approved", False)
        entry["event"].set()
    _pending_approvals.clear()
    for entry in _pending_plan_approvals.values():
        entry["mode"] = ""
        entry["event"].set()
    _pending_plan_approvals.clear()


async def _handle_chat(ws: WebSocket, msg: dict, A, C):
    """Run an agent turn in a thread, stream events to the WebSocket."""
    text = msg.get("text", "")
    if not text.strip():
        return
    try:
        attachments = _validated_attachments(msg.get("attachments", []), A, C)
    except ValueError as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        return

    _interrupt_event.clear()
    S = C.S

    # Append user message
    if attachments:
        base = A.ARTIFACTS_ROOT.resolve()
        refs = []
        for attachment in attachments:
            # Relative artifact paths are usable by read_text but do not disclose
            # a host filesystem location in history/export responses.
            relative = Path(attachment["path"]).resolve().relative_to(base)
            refs.append(f"- {attachment['name']} (attachment {attachment['id']}): {relative}")
        text += "\n\nAttached session artifacts (read these files when needed):\n" + "\n".join(refs)
    S.messages.append({"role": "user", "content": text})

    trace = [] if S.show_trace else None
    turn_start = time.time()
    tokens_ref = [0]
    scrubber = _StreamScrubber()  # per-turn: strips tool-call markup from the live stream

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
        # Count characters, not chunks — each callback is one streaming delta
        # of arbitrary size, so += 1 wildly overstated "tokens".
        tokens_ref[0] += len(delta)
        # Strip tool-call protocol from the live stream (web equivalent of the
        # CLI's ProseStream) so markup never flashes in the chat bubble.
        clean = scrubber.feed(delta)
        if not clean:
            return
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "token", "kind": kind, "delta": clean}),
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
            ws.send_json({"type": "tool_result", "name": name,
                          "result": scrub_markup(result)[:5000]}),
            loop,
        )

    def on_escalation(name, result):
        """Approval flow — send to WebSocket, wait for a keyed response.

        Each escalation gets a fresh id + state so a timed-out or superseded
        prompt can never read a verdict meant for a different escalation.
        """
        esc_id = f"esc-{int(time.time()*1000)}-{id(result)}"
        entry = {"event": threading.Event(), "approved": False, "session_scope": False}
        _pending_approvals[esc_id] = entry
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "escalation", "tool_name": name,
                          "change_type": "write",
                          "description": scrub_markup(result)[:1000],
                          "id": esc_id}),
            loop,
        )
        entry["event"].wait(timeout=300)
        entry = _pending_approvals.pop(esc_id, entry)
        approved = entry.get("approved", False)
        if approved:
            A.grant_escalation()
        return approved

    def _plan_on_step(idx, total, step_text, tool_name, status, result):
        """Render plan checklists in the UI (mirrors the CLI's _plan_on_step)."""
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "plan_step", "index": idx, "total": total,
                          "step_text": step_text, "tool_name": tool_name,
                          "status": status, "result": result}),
            loop,
        )

    def _plan_on_escalation(escalation_text):
        """Route plan write-step escalations to the ApprovalCard."""
        return on_escalation("plan", escalation_text)

    def _plan_on_approval(escalation_text):
        """Plan (execute_plan) approval — keyed like tool escalations."""
        plan_id = f"plan-{int(time.time()*1000)}-{id(escalation_text)}"
        entry = {"event": threading.Event(), "mode": ""}
        _pending_plan_approvals[plan_id] = entry
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "plan_approval", "plan": escalation_text[:2000],
                          "id": plan_id}),
            loop,
        )
        entry["event"].wait(timeout=300)
        entry = _pending_plan_approvals.pop(plan_id, entry)
        return entry.get("mode", "") == "approved"

    def on_answer(answer):
        elapsed = time.time() - turn_start
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "answer", "text": scrub_markup(answer),
                          "usage": {"seconds": elapsed, "tokens": tokens_ref[0],
                                    "context": C._estimate_context_pct()}}),
            loop,
        )

    # Run the agent in a thread to not block the event loop
    def _run():
        try:
            # Wire plan execution callbacks so execute_plan renders the
            # checklist and routes escalations to the UI (CLI does the same
            # in do_chat; without these, plan-only mode dead-ends in the UI).
            A._plan_on_step = _plan_on_step
            A._plan_on_escalation = _plan_on_escalation
            A._plan_on_approval = _plan_on_approval
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
                ws.send_json({"type": "error", "message": scrub_markup(str(exc))}),
                loop,
            )
        finally:
            A._plan_on_step = None
            A._plan_on_escalation = None
            A._plan_on_approval = None

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    # Await thread completion without blocking the event loop
    await loop.run_in_executor(None, thread.join)


async def _handle_command(ws: WebSocket, msg: dict, A, C):
    """Execute a slash command and return the result."""
    command = str(msg.get("command", "")).strip().lstrip("/")
    args = msg.get("args", "")
    if command.lower() == "plan":
        # cmd_plan() delegates inline tasks to the terminal-only do_chat().
        # The web runner owns the equivalent streamed path.
        A.enter_plan_mode()
        if str(args).strip():
            await _handle_chat(ws, {"text": str(args)}, A, C)
        else:
            await ws.send_json({"type": "command_result", "command": command,
                                "result": "plan mode — reads only. Agent8088 will research, propose a "
                                          "plan, and wait for your approval before anything is written or run."})
        return
    if command.lower() in {"exit", "quit"}:
        await ws.send_json({"type": "command_result", "command": command,
                            "result": f"/{command} is CLI-only and does nothing in the browser."})
        return
    if command.lower() == "agent" and not str(args).strip():
        await ws.send_json({"type": "command_result", "command": command,
                            "result": "cancelled — try /agent <name> <task>, or /agents to list them"})
        return
    if command.lower() == "fusion" and str(args).strip().lower() == "setup":
        await ws.send_json({"type": "command_result", "command": command,
                            "result": "Use Settings → Fusion to configure the panel and judge in the web UI."})
        return
    if command.lower() == "agents" and str(args).strip().lower().split(" ", 1)[0] in {"new", "delete"}:
        await ws.send_json({"type": "command_result", "command": command,
                            "result": "Use Settings → Sub-Agents to manage profiles in the web UI."})
        return
    if command.lower() == "agents" and str(args).strip().lower().startswith("edit"):
        await ws.send_json({"type": "command_result", "command": command,
                            "result": "/agents edit opens a local terminal editor and is not available in the web UI."})
        return
    model_arg = str(args).strip().lower()
    if ((command.lower() == "models" and (not model_arg or model_arg in C.A.PROVIDERS or
                                             model_arg in {"custom", "selfhosted", "self-hosted"})) or
            (command.lower() == "model" and model_arg == "setup")):
        await ws.send_json({"type": "command_result", "command": command,
                            "result": "Use Settings → Config → Model Switcher for interactive model selection and setup. "
                                      "You can still switch directly with /model <provider>[:model]."})
        return
    handler = C.COMMANDS.get(command.lower())
    if not handler:
        await ws.send_json({"type": "command_result", "command": command,
                             "result": f"unknown command: /{command}"})
        return
    try:
        # Capture console output — cmd_* functions print to Rich console.
        # Run the handler off the event loop: some commands (doctor, dump,
        # mcp) probe the network or shell and can take seconds.
        import io
        from rich.console import Console as RichConsole
        loop = asyncio.get_running_loop()

        def _exec_command():
            buf = io.StringIO()
            temp_console = RichConsole(file=buf, force_terminal=False, no_color=True, width=120)
            original_console = C.console
            C.console = temp_console
            try:
                handler(args)
            finally:
                C.console = original_console
            return buf.getvalue()

        output = await loop.run_in_executor(None, _exec_command)
        await ws.send_json({"type": "command_result", "command": command,
                            "result": scrub_markup(output)})
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
            # An unmatched /api path is a client error, not a route into the app.
            # Serving index.html here (HTTP 200, text/html) meant a caller hitting
            # a renamed endpoint got HTML and a JSON parse error instead of a 404.
            if full_path == "api" or full_path.startswith("api/"):
                return _error(f"no such endpoint: /{full_path}", 404)
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
        if not dist_dir.exists():
            log.warning(
                "web/dist not found — production UI will 404. "
                "Run 'cd web && npm install && npm run build' first, or use --web-dev."
            )
        _mount_static(app, dist_dir)
    print(f"Agent8088 web UI on http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
