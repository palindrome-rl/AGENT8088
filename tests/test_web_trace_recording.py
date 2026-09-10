"""Reported live: the WebUI's "Show trace" preference turned on trace
COLLECTION (web_server.py sets trace = [] when S.show_trace), but the built
trace was never recorded anywhere -- unlike the CLI's do_chat, which calls
_record_trace() on every turn. GET /api/history (what the Sessions page's
trace viewer reads) stayed permanently empty regardless of the toggle.
"""
import asyncio

from agent8088 import cli, web_server


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


class _Engine:
    class AgentInterrupted(Exception):
        pass

    def __init__(self, artifacts_root, trace_steps=None, raise_interrupted=False):
        self.PERMISSION_MODE = "full-auto"
        self.ARTIFACTS_ROOT = artifacts_root
        self._trace_steps = trace_steps or []
        self._raise_interrupted = raise_interrupted

    def build_tools_def(self, specs):
        return []

    def run_agent(self, messages, **kwargs):
        trace = kwargs.get("trace")
        if trace is not None:
            trace.extend(self._trace_steps)
        if self._raise_interrupted:
            raise self.AgentInterrupted()
        on_answer = kwargs.get("on_answer")
        if on_answer:
            on_answer("done")
        return "done"

    def current_rate_limit_status(self, provider):
        return None


def _run_turn(monkeypatch, tmp_path, trace_steps=None, show_trace=True, raise_interrupted=False):
    fresh_session = cli.Session()
    fresh_session.show_trace = show_trace
    monkeypatch.setattr(cli, "S", fresh_session)
    monkeypatch.setattr(web_server, "_validated_attachments", lambda *a, **k: [])
    engine = _Engine(tmp_path, trace_steps=trace_steps, raise_interrupted=raise_interrupted)
    socket = _Socket()
    asyncio.run(web_server._handle_chat(socket, {"text": "hello"}, engine, cli))
    return fresh_session


def test_trace_recorded_on_successful_turn(monkeypatch, tmp_path):
    steps = [{"tool": "read_text", "args": {"filename": "x"}}]
    session = _run_turn(monkeypatch, tmp_path, trace_steps=steps, show_trace=True)

    assert len(session.conversation_trace) == 1
    entry = session.conversation_trace[0]
    assert entry["input"] == "hello"
    assert entry["steps"] == steps
    assert entry["interrupted"] is False


def test_trace_not_recorded_when_show_trace_off(monkeypatch, tmp_path):
    session = _run_turn(monkeypatch, tmp_path, trace_steps=[{"tool": "x"}], show_trace=False)

    assert session.conversation_trace == []


def test_trace_recorded_on_interrupted_turn(monkeypatch, tmp_path):
    session = _run_turn(monkeypatch, tmp_path, trace_steps=[{"tool": "x"}], show_trace=True,
                        raise_interrupted=True)

    assert len(session.conversation_trace) == 1
    assert session.conversation_trace[0]["interrupted"] is True


def test_api_history_reflects_the_recorded_trace(monkeypatch, tmp_path):
    session = _run_turn(monkeypatch, tmp_path, trace_steps=[{"tool": "read_text"}], show_trace=True)
    monkeypatch.setattr(web_server, "_cl", lambda: cli)
    monkeypatch.setattr(cli, "S", session)

    result = asyncio.run(web_server.get_history())

    assert result["conversation_trace"] == session.conversation_trace
    assert len(result["conversation_trace"]) == 1
