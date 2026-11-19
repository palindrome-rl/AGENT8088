"""/resume restores a saved session's fields onto S, but the browser-use
console logging verbosity (browse_page's _set_browser_use_log_verbosity call)
reads engine.py's own A.SHOW_REASONING global, not S.show_reasoning directly -
see cmd_reasoning, which sets both on every toggle. /resume must do the same,
or a session saved with reasoning on leaves that global (and therefore
browser-use's console noise) silently out of sync with what /resume just
told the user it restored.
"""
import json

from agent8088 import engine as A
from agent8088 import cli


def _write_session(sessions_dir, name, **fields):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "name": name, "messages": [], **fields}
    (sessions_dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_resume_resyncs_show_reasoning_onto_the_engine_global(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(cli, "SESSIONS_DIR", sessions_dir)
    _write_session(sessions_dir, "mysession", show_reasoning=True)
    monkeypatch.setattr(cli.S, "show_reasoning", False)
    monkeypatch.setattr(cli.S, "name", "")
    monkeypatch.setattr(A, "SHOW_REASONING", False)

    cli.cmd_resume("mysession")

    assert cli.S.show_reasoning is True
    assert A.SHOW_REASONING is True


def test_resume_resyncs_show_reasoning_off_too(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(cli, "SESSIONS_DIR", sessions_dir)
    _write_session(sessions_dir, "mysession", show_reasoning=False)
    monkeypatch.setattr(cli.S, "show_reasoning", True)
    monkeypatch.setattr(cli.S, "name", "")
    monkeypatch.setattr(A, "SHOW_REASONING", True)

    cli.cmd_resume("mysession")

    assert cli.S.show_reasoning is False
    assert A.SHOW_REASONING is False
