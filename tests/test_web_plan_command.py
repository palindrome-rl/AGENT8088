"""Web-only routing for inline /plan tasks."""

import asyncio

from agent8088 import web_server


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


class _Engine:
    def __init__(self):
        self.entered_plan_mode = False

    def enter_plan_mode(self):
        self.entered_plan_mode = True
        self.PERMISSION_MODE = "plan-only"


def test_mode_endpoint_enters_plan_mode(monkeypatch):
    engine = _Engine()
    engine.PERMISSION_MODE = "readonly"
    monkeypatch.setattr(web_server, "_eng", lambda: engine)

    result = asyncio.run(web_server.set_mode(web_server.ModeBody(mode="plan-only")))

    assert engine.entered_plan_mode
    assert result == {"ok": True, "mode": "plan-only"}


def test_inline_plan_uses_web_chat_runner(monkeypatch):
    seen = []

    async def fake_handle_chat(ws, msg, engine, cli):
        seen.append((ws, msg, engine, cli))

    monkeypatch.setattr(web_server, "_handle_chat", fake_handle_chat)
    socket, engine, cli = _Socket(), _Engine(), object()

    asyncio.run(web_server._handle_command(socket, {"command": "plan", "args": "audit the app"}, engine, cli))

    assert engine.entered_plan_mode
    assert seen == [(socket, {"text": "audit the app"}, engine, cli)]
    assert socket.events == []


class _PlanEngine(_Engine):
    """Engine stub whose run_agent exercises the plan-approval callback.

    present_plan feeds this callback's return value straight into
    set_permission_mode(), so the contract is a permission-mode STRING
    ("full-auto"/"readonly"), or "" to stay in plan mode - never a bool.
    """

    class AgentInterrupted(Exception):
        pass

    def __init__(self, artifacts_root):
        super().__init__()
        self.PERMISSION_MODE = "plan-only"
        self.ARTIFACTS_ROOT = artifacts_root
        self.chosen = "unset"

    def build_tools_def(self, specs):
        return []

    def grant_escalation(self):
        pass

    def run_agent(self, messages, **kwargs):
        self.chosen = self._plan_on_approval("## Plan\n1. do the thing")
        return "done"


class _PlanSession:
    def __init__(self):
        self.messages = []
        self.show_trace = False
        self.temperature = 0.0
        self.name = ""
        self.last_usage = {}
        self.trajectory_state = {}


class _PlanCli:
    def __init__(self):
        self.S = _PlanSession()
        self._session_system_prompt = "sys"

    def _turn_max_turns(self, mode):
        return 25

    def _estimate_context_pct(self):
        return 0

    def _save_active_session(self):
        pass

    def _active_tool_specs(self):
        return {}


class _ApprovingSocket:
    """Answers a plan_approval frame the way PlanApprovalCard does."""

    def __init__(self, mode):
        self.mode = mode
        self.events = []

    async def send_json(self, event):
        self.events.append(event)
        if event.get("type") == "plan_approval":
            entry = web_server._pending_plan_approvals.get(event["id"])
            if entry is not None:
                entry["mode"] = self.mode
                entry["event"].set()


def _run_plan_approval(monkeypatch, tmp_path, mode):
    engine = _PlanEngine(tmp_path)
    cli = _PlanCli()
    socket = _ApprovingSocket(mode)
    monkeypatch.setattr(web_server, "_validated_attachments", lambda *a, **k: [])
    asyncio.run(web_server._handle_chat(socket, {"text": "plan it"}, engine, cli))
    return engine, socket


def test_plan_approval_returns_permission_mode_string(monkeypatch, tmp_path):
    """Approving in the browser must hand present_plan a real mode to run in."""
    engine, socket = _run_plan_approval(monkeypatch, tmp_path, "full-auto")

    assert any(e.get("type") == "plan_approval" for e in socket.events)
    assert engine.chosen == "full-auto"


def test_plan_approval_readonly_mode_is_passed_through(monkeypatch, tmp_path):
    engine, _ = _run_plan_approval(monkeypatch, tmp_path, "readonly")

    assert engine.chosen == "readonly"


def test_plan_decline_stays_in_plan_mode(monkeypatch, tmp_path):
    """Declining must be falsy so present_plan keeps planning."""
    engine, _ = _run_plan_approval(monkeypatch, tmp_path, "")

    assert engine.chosen == ""
