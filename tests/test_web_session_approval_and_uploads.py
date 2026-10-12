"""Web approvals that last a session, and uploads stored under their real name."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from agent8088 import document_access
from agent8088 import engine as real_engine
from agent8088 import web_server as web


# --- "Approve for session" ---------------------------------------------------

class _EscalatingEngine:
    """run_agent asks for each escalation in `requests` and records the answers."""

    class AgentInterrupted(Exception):
        pass

    _unwrap_untrusted = staticmethod(real_engine._unwrap_untrusted)

    def __init__(self, artifacts_root, requests, mode="readonly"):
        self.ARTIFACTS_ROOT = artifacts_root
        self.PERMISSION_MODE = mode
        self.requests = requests
        self.answers = []
        self.grants = []

    def build_tools_def(self, specs):
        return []

    def grant_escalation(self, change_type="", target=""):
        self.grants.append((change_type, target))

    def run_agent(self, messages, **kwargs):
        for name, request in self.requests:
            self.answers.append(kwargs["on_escalation"](name, request))
        return "done"


class _Session:
    def __init__(self):
        self.messages = []
        self.show_trace = False
        self.temperature = 0.0
        self.name = ""
        self.last_usage = {}
        self.trajectory_state = {}


class _Cli:
    def __init__(self):
        self.S = _Session()
        self._session_system_prompt = "sys"

    def _turn_max_turns(self, mode, session=None):
        return 25

    def _estimate_context_pct(self, session=None):
        return 0

    def _save_active_session(self):
        pass

    def _active_tool_specs(self, session=None):
        return {}


class _ApprovingSocket:
    """Answers escalation frames the way ApprovalCard does."""

    def __init__(self, session_scope):
        self.session_scope = session_scope
        self.events = []

    async def send_json(self, event):
        self.events.append(event)
        if event.get("type") == "escalation":
            entry = web._pending_approvals.get(event["id"])
            if entry is not None:
                entry["approved"] = True
                entry["session_scope"] = self.session_scope
                entry["event"].set()

    def prompts(self):
        return [e for e in self.events if e.get("type") == "escalation"]


def _request(change_type, paths=("web_search: news",)):
    return real_engine.request_escalation("edit", list(paths), change_type, "reason")


def _turn(monkeypatch, engine, socket, cli=None):
    monkeypatch.setattr(web, "_validated_attachments", lambda *a, **k: [])
    asyncio.run(web._handle_chat(socket, {"text": "go"}, engine, cli or _Cli()))


@pytest.fixture(autouse=True)
def _fresh_allowlist():
    allowlist = getattr(web, "_session_allowlist", set())
    allowlist.clear()
    yield
    allowlist.clear()


def test_session_approval_stops_asking_for_that_change_type(monkeypatch, tmp_path):
    request = ("web_search", _request("network_request"))
    engine = _EscalatingEngine(tmp_path, [request, request])
    socket = _ApprovingSocket(session_scope=True)
    _turn(monkeypatch, engine, socket)

    assert len(socket.prompts()) == 1, "the second search must not ask again"
    assert engine.answers == [True, True]
    assert engine.grants == [("network_request", "web_search: news")] * 2

    # And it outlives the turn: the next turn's search is not asked about either.
    later = _EscalatingEngine(tmp_path, [request])
    later_socket = _ApprovingSocket(session_scope=True)
    _turn(monkeypatch, later, later_socket)
    assert later_socket.prompts() == []
    assert later.answers == [True]


def test_approve_once_still_asks_next_time(monkeypatch, tmp_path):
    request = ("web_search", _request("network_request"))
    engine = _EscalatingEngine(tmp_path, [request, request])
    socket = _ApprovingSocket(session_scope=False)
    _turn(monkeypatch, engine, socket)

    assert len(socket.prompts()) == 2


def test_card_shows_the_real_change_type(monkeypatch, tmp_path):
    engine = _EscalatingEngine(tmp_path, [("web_search", _request("network_request"))])
    socket = _ApprovingSocket(session_scope=False)
    _turn(monkeypatch, engine, socket)

    assert socket.prompts()[0]["change_type"] == "network_request"


def test_git_push_grant_names_its_target_and_is_never_session_wide(monkeypatch, tmp_path):
    push = ("git_push", _request("git_remote_write", ["origin main"]))
    engine = _EscalatingEngine(tmp_path, [push, push])
    socket = _ApprovingSocket(session_scope=True)
    _turn(monkeypatch, engine, socket)

    # A bare grant never matched the push target, so the push could not run.
    assert engine.grants[0] == ("git_remote_write", "origin main")
    assert len(socket.prompts()) == 2, "a push is approved per push"
    assert "git_remote_write" not in web._session_allowlist


def test_plan_approval_is_never_session_wide(monkeypatch, tmp_path):
    plan = ("plan", _request("plan_approval", ["(plan)"]))
    engine = _EscalatingEngine(tmp_path, [plan, plan])
    socket = _ApprovingSocket(session_scope=True)
    _turn(monkeypatch, engine, socket)

    assert len(socket.prompts()) == 2


def test_session_scope_is_ignored_in_plan_only_mode(monkeypatch, tmp_path):
    request = ("write_file", _request("write", ["a.txt"]))
    engine = _EscalatingEngine(tmp_path, [request, request], mode="plan-only")
    socket = _ApprovingSocket(session_scope=True)
    _turn(monkeypatch, engine, socket)

    assert len(socket.prompts()) == 2


def test_wrapped_plan_step_request_is_still_read(monkeypatch, tmp_path):
    # Shell-mode plan steps reach the handler wrapped like this.
    wrapped = real_engine._wrap_untrusted(_request("shell", ["ls"]), "shell command: ls")
    engine =_EscalatingEngine(tmp_path, [("plan", wrapped)])
    socket = _ApprovingSocket(session_scope=False)
    _turn(monkeypatch, engine, socket)

    assert engine.answers == [True]
    assert engine.grants == [("shell", "ls")]


def test_malformed_request_fails_closed_without_asking(monkeypatch, tmp_path):
    engine = _EscalatingEngine(tmp_path, [("web_search", "not an escalation")])
    socket = _ApprovingSocket(session_scope=True)
    _turn(monkeypatch, engine, socket)

    assert engine.answers == [False]
    assert socket.prompts() == []


def test_new_and_resumed_sessions_start_with_no_session_approvals(monkeypatch, tmp_path):
    session_file = tmp_path / "saved.json"
    session_file.write_text(json.dumps({"messages": []}), encoding="utf-8")
    cli = SimpleNamespace(
        S=SimpleNamespace(max_turns=50, name=""), _save_active_session=lambda: None,
        _session_name=lambda name: name, _session_path=lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(web, "_cl", lambda: cli)

    web._session_allowlist.add("network_request")
    asyncio.run(web.new_session(web.SessionActionBody(name="")))
    assert not web._session_allowlist

    web._session_allowlist.add("network_request")
    asyncio.run(web.resume_session(web.SessionActionBody(name="saved")))
    assert not web._session_allowlist


def test_resume_replaces_unknown_display_settings_with_defaults(monkeypatch, tmp_path):
    (tmp_path / "odd.json").write_text(
        json.dumps({"messages": [], "verbose": "loud", "usage_mode": "all"}), encoding="utf-8")
    cli = SimpleNamespace(
        S=SimpleNamespace(max_turns=50, name=""), _save_active_session=lambda: None,
        _session_name=lambda name: name, _session_path=lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(web, "_cl", lambda: cli)

    asyncio.run(web.resume_session(web.SessionActionBody(name="odd")))
    assert (cli.S.verbose, cli.S.usage_mode) == ("on", "tokens")


# --- Preferences come back from /api/config ----------------------------------

def test_config_returns_every_preference_the_form_shows(monkeypatch):
    from agent8088 import cli

    monkeypatch.setattr(cli.S, "verbose", "full")
    monkeypatch.setattr(cli.S, "usage_mode", "off")
    monkeypatch.setattr(cli.S, "show_trace", True)
    monkeypatch.setattr(cli.S, "show_reasoning", True)

    config = asyncio.run(web.get_config())
    assert (config["verbose"], config["usage_mode"]) == ("full", "off")
    assert config["show_trace"] is True and config["show_reasoning"] is True


# --- Uploads keep their real name -------------------------------------------

def _upload_request(name, body):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "headers": [(b"x-filename", name.encode())]}, receive)


def _upload_setup(monkeypatch, tmp_path):
    a = SimpleNamespace(ARTIFACTS_ROOT=tmp_path, _is_sensitive_path=real_engine._is_sensitive_path)
    c = SimpleNamespace(S=SimpleNamespace(name="test-session"))
    monkeypatch.setattr(web, "_eng", lambda: a)
    monkeypatch.setattr(web, "_cl", lambda: c)
    return a, c


def _upload(name, body=b"alpha"):
    return asyncio.run(web.upload_attachment(_upload_request(name, body)))


def test_upload_is_stored_under_its_real_name(monkeypatch, tmp_path):
    a, c = _upload_setup(monkeypatch, tmp_path)
    result = _upload("F1 race plan.md")
    path = web._validated_attachments([result["id"]], a, c)[0]["path"]

    assert path.endswith(f"{result['id']}\\F1 race plan.md") or path.endswith(
        f"{result['id']}/F1 race plan.md")


def test_upload_still_resolves_after_document_processing(monkeypatch, tmp_path):
    a, c = _upload_setup(monkeypatch, tmp_path)
    result = _upload("notes.txt")
    path = web._validated_attachments([result["id"]], a, c)[0]["path"]
    # Leaves a .document-jobs folder beside the file, inside the upload's folder.
    document_access.process(path, "task", lambda *args: "evidence")

    assert web._validated_attachments([result["id"]], a, c)[0]["path"] == path


def test_delete_removes_the_upload_folder(monkeypatch, tmp_path):
    a, c = _upload_setup(monkeypatch, tmp_path)
    result = _upload("notes.txt")
    path = web._validated_attachments([result["id"]], a, c)[0]["path"]
    document_access.process(path, "task", lambda *args: "evidence")

    asyncio.run(web.delete_attachment(result["id"]))
    assert not list(tmp_path.rglob(result["id"] + "*"))


@pytest.mark.parametrize("name", ["CON.txt", "nul.md", "db_password_notes.txt"])
def test_names_that_cannot_be_kept_fall_back_to_the_opaque_name(monkeypatch, tmp_path, name):
    a, c = _upload_setup(monkeypatch, tmp_path)
    result = _upload(name)
    path = web._validated_attachments([result["id"]], a, c)[0]["path"]

    assert path.endswith(result["id"] + "." + name.rsplit(".", 1)[1])
    assert open(path, "rb").read() == b"alpha"


def test_uploads_saved_before_the_change_still_open(monkeypatch, tmp_path):
    a, c = _upload_setup(monkeypatch, tmp_path)
    index = web._attachment_index(a, c)
    old_id = "b" * 32
    (index.parent / f"{old_id}.txt").write_bytes(b"old")
    index.write_text(json.dumps({old_id: {"name": "old.txt", "size": 3, "type": "txt"}}),
                     encoding="utf-8")

    path = web._validated_attachments([old_id], a, c)[0]["path"]
    assert open(path, "rb").read() == b"old"
    asyncio.run(web.delete_attachment(old_id))
    assert not (index.parent / f"{old_id}.txt").exists()
