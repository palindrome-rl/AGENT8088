"""CLI conversations are saved without /new: the first REPL chat turn creates
a session, every turn/interrupt/exit rewrites it atomically, and the files
stay compatible with older readers and the Web UI (SESSION-FIX-DESIGN.md, Fix 2).
"""
import io
import json
import re
import sys
import threading
from contextlib import nullcontext

import pytest
from rich.console import Console

from agent8088 import cli


@pytest.fixture
def out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, force_terminal=False, width=200))
    return buf


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    fresh = cli.Session()
    fresh.autosave = True  # as inside the REPL
    monkeypatch.setattr(cli, "S", fresh)
    directory = tmp_path / "sessions"
    monkeypatch.setattr(cli, "SESSIONS_DIR", directory)
    monkeypatch.setattr(cli.A, "ACTIVE_PROVIDER", "prov")
    monkeypatch.setattr(cli.A, "MODEL_NAME", "model-a")
    return directory


class _Esc:
    _active = False  # do_chat reads it to decide whether to show the hint

    def __init__(self):
        self.triggered = threading.Event()
        self._active = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def paused(self):
        return nullcontext()


def _chat(monkeypatch, query, run_agent=None):
    def answer(messages, **kwargs):
        messages.append({"role": "assistant", "content": "ok"})
        return "ok"

    monkeypatch.setattr(cli, "EscListener", _Esc)
    monkeypatch.setattr(cli.A, "run_agent", run_agent or answer)
    monkeypatch.setattr(cli, "_await_memory_capture", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_after_turn_plan_state", lambda: None)
    cli.do_chat(query)


def _load(directory, name):
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))


def test_first_prompt_autosaves_and_appears_in_sessions(monkeypatch, out, sessions):
    _chat(monkeypatch, "Fix the login bug, please!")
    assert cli.S.name == "fix-the-login-bug-please"
    data = _load(sessions, cli.S.name)
    assert [m["content"] for m in data["messages"]] == ["Fix the login bug, please!", "ok"]
    assert re.fullmatch(r"[0-9a-f]{32}", data["id"])
    assert data["source"] == "cli" and data["model"] == "prov:model-a"
    assert data["version"] == 2 and data["created_at"] and data["updated_at"]
    assert "chat saved as fix-the-login-bug-please" in out.getvalue()
    cli.cmd_sessions("")
    assert "fix-the-login-bug-please" in out.getvalue()


def test_one_shot_runs_stay_ephemeral(monkeypatch, out, sessions):
    cli.S.autosave = False
    _chat(monkeypatch, "hello")
    assert cli.S.name == "" and not sessions.exists()


def test_autosave_names_never_overwrite_existing_sessions(monkeypatch, out, sessions):
    sessions.mkdir()
    (sessions / "hello.json").write_text('{"keep": 1}', encoding="utf-8")
    (sessions / "hello-2.json").write_text('{"keep": 2}', encoding="utf-8")
    _chat(monkeypatch, "hello")
    assert cli.S.name == "hello-3"
    assert _load(sessions, "hello") == {"keep": 1}
    assert _load(sessions, "hello-2") == {"keep": 2}


def test_prompt_without_letters_gets_a_timestamp_name(monkeypatch, out, sessions):
    _chat(monkeypatch, "???")
    assert cli.S.name.startswith("chat-")


def test_interrupted_turn_is_saved(monkeypatch, out, sessions):
    def interrupted(messages, **kwargs):
        messages.append({"role": "assistant", "content": "partial work"})
        raise cli.A.AgentInterrupted()

    _chat(monkeypatch, "long task", run_agent=interrupted)
    contents = [m["content"] for m in _load(sessions, "long-task")["messages"]]
    assert contents == ["long task", "partial work"]


def test_ctrl_c_and_errors_in_the_repl_save_progress(monkeypatch, out, sessions):
    feed = iter(["first question", "second question"])

    def read_line():
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    def chat(line):
        cli._ensure_autosave_session(line)
        cli.S.messages.append({"role": "user", "content": line})
        if line == "first question":
            raise RuntimeError("model exploded")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_read_line", read_line)
    monkeypatch.setattr(cli, "do_chat", chat)
    monkeypatch.setattr(cli, "_mcp_failure_notice", lambda: [])
    monkeypatch.setattr(cli, "_print_capability_changes", lambda: None)
    cli._repl_loop()
    # Error path saved the first question; Ctrl+C leaves for main()'s exit save.
    assert len(_load(sessions, "first-question")["messages"]) == 1
    assert cli.S.autosave is False
    cli._save_session_safely()  # what main()'s finally does on the way out
    assert len(_load(sessions, "first-question")["messages"]) == 2


def test_exit_saves_latest_state(monkeypatch, out, sessions):
    _chat(monkeypatch, "remember this")
    cli.S.messages.append({"role": "user", "content": "unsent follow-up"})
    feed = iter(["/exit"])
    monkeypatch.setattr(cli, "_read_line", lambda: next(feed))
    for name in ("configure_logging", "_install_completion", "_first_run_check",
                 "banner", "warn_about_unknown_theme"):
        monkeypatch.setattr(cli, name, lambda *a, **k: None)
    monkeypatch.setattr(cli.A, "resolve_auto_search_provider", lambda: None)
    monkeypatch.setattr(cli.A, "verify_sandbox_backend", lambda: None)
    monkeypatch.setattr(cli, "_startup_ollama_model_check", lambda: [])
    monkeypatch.setattr(cli, "_startup_notices", lambda extra=(): [])
    monkeypatch.setattr(cli, "_mcp_failure_notice", lambda: [])
    monkeypatch.setattr(cli, "_flush_memory_capture", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["agent8088"])
    cli.main()
    assert _load(sessions, "remember-this")["messages"][-1]["content"] == "unsent follow-up"


def test_failed_save_keeps_old_file_and_warns_once(monkeypatch, out, sessions):
    _chat(monkeypatch, "keep me")
    before = (sessions / "keep-me.json").read_text(encoding="utf-8")

    def broken(path, content):
        raise OSError("disk full")

    monkeypatch.setattr(cli.A, "_write_private_text", broken)
    cli.S.messages.append({"role": "user", "content": "lost?"})
    assert cli._save_session_safely() is False
    assert cli._save_session_safely() is False
    assert (sessions / "keep-me.json").read_text(encoding="utf-8") == before
    assert out.getvalue().count("could not save session 'keep-me': disk full") == 1
    assert not [p for p in sessions.iterdir() if p.name != "keep-me.json"]


def test_a_save_error_does_not_break_the_turn(monkeypatch, out, sessions):
    def answer(messages, **kwargs):
        kwargs["on_trajectory_state"]({})
        messages.append({"role": "assistant", "content": "done"})
        return "done"

    monkeypatch.setattr(cli, "save_session_file", lambda path, data: (_ for _ in ()).throw(OSError("ro")))
    _chat(monkeypatch, "anything", run_agent=answer)  # must not raise
    assert "could not save session" in out.getvalue()


def test_explicit_new_and_resume_still_work(monkeypatch, out, sessions):
    cli.cmd_new("work")
    assert cli.S.name == "work"
    _chat(monkeypatch, "first")
    assert cli.S.name == "work"  # no autosave name when one is set
    assert len(_load(sessions, "work")["messages"]) == 2
    cli.S.messages.clear()
    cli.S.name = ""
    cli.cmd_resume("work")
    assert [m["content"] for m in cli.S.messages] == ["first", "ok"]


def test_model_change_keeps_the_conversation(monkeypatch, out, sessions):
    _chat(monkeypatch, "switch models")
    monkeypatch.setattr(cli.A, "MODEL_NAME", "model-b")
    _chat(monkeypatch, "again")
    data = _load(sessions, "switch-models")
    assert len(data["messages"]) == 4
    assert data["model"] == "prov:model-b"


def test_old_session_file_loads_and_gains_identity(monkeypatch, out, sessions):
    sessions.mkdir()
    old = {"version": 1, "name": "legacy",
           "messages": [{"role": "user", "content": "from before"}]}
    (sessions / "legacy.json").write_text(json.dumps(old), encoding="utf-8")
    cli.cmd_resume("legacy")
    assert cli.S.messages == old["messages"]
    cli.cmd_sessions("")
    assert "legacy" in out.getvalue()
    cli._save_active_session()
    data = _load(sessions, "legacy")
    assert re.fullmatch(r"[0-9a-f]{32}", data["id"]) and data["source"] == "cli"


def test_id_source_and_created_at_are_kept_across_saves(monkeypatch, out, sessions):
    sessions.mkdir()
    web = {"version": 2, "id": "a" * 32, "name": "webchat", "source": "web",
           "created_at": "2026-01-01T00:00:00+00:00", "messages": []}
    (sessions / "webchat.json").write_text(json.dumps(web), encoding="utf-8")
    cli.cmd_resume("webchat")
    _chat(monkeypatch, "continue in the cli")
    data = _load(sessions, "webchat")
    assert data["id"] == "a" * 32 and data["source"] == "web"
    assert data["created_at"] == "2026-01-01T00:00:00+00:00"
    assert data["updated_at"] != data["created_at"]
