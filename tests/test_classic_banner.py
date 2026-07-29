import io
import json

from rich.console import Console

import agent8088.cli as classic


def test_classic_banner_includes_brand_and_catalogues(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=180, color_system=None))

    classic.banner()

    rendered = output.getvalue()
    assert "AGENT8088" in rendered
    assert "ÔûêÔûêÔûêÔûêÔûêÔòù  ÔûêÔûêÔûêÔûêÔûêÔûêÔòù" in rendered
    assert classic._PALINDROME_LOGO.is_file()
    logo = classic._palindrome_logo().plain
    assert "ÔûÇ" in logo
    assert max(map(len, logo.splitlines())) == 24
    assert len(classic._classic_masthead().spans) == 6
    assert "Palindrome" in rendered
    assert "Research Labs" in rendered
    assert "Available Tools" in rendered
    assert "Available Skills" in rendered
    assert classic._catalog(["delta", "alpha"], columns=1) == "alpha\ndelta"


def test_classic_masthead_compacts_on_narrow_terminals(monkeypatch):
    monkeypatch.setattr(classic, "console", Console(file=io.StringIO(), width=79, color_system=None))

    assert "/_\\ / __| __|" in classic._classic_masthead().plain
    monkeypatch.setattr(classic, "console", Console(file=io.StringIO(), width=80, color_system=None))
    assert "ÔûêÔûêÔûêÔûêÔûêÔòù  ÔûêÔûêÔûêÔûêÔûêÔûêÔòù" in classic._classic_masthead().plain


def test_command_suggestions_cover_slash_and_bare_prefixes():
    assert "/help" in classic._command_matches("/")
    assert "/quit" in classic._command_matches("/")
    assert classic._command_matches("m", slash=False) == ["maxturns", "model"]
    assert classic._live_matches("/")[1] == classic._command_matches("/")
    assert classic._live_matches("/m")[1] == ["/maxturns", "/model"]
    assert classic._live_matches("m") == ("m", ["maxturns", "model"])


def test_default_skills_are_loaded_into_the_agent_and_status(monkeypatch):
    expected = {"plan", "systematic-debugging", "test-driven-development", "github-code-review", "documentation-writing"}
    assert expected <= set(classic.A.SKILL_PACKAGES)
    assert "## Installed skills" in classic.A.SYSTEM_PROMPT
    assert classic.A.SKILL_PACKAGES["plan"]["category"] == "workflow"

    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    classic.cmd_status("")
    assert "Session Status" in output.getvalue()


def test_named_session_round_trips_skill_state(tmp_path, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(classic, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(classic.S, "name", "")
    monkeypatch.setattr(classic.S, "messages", [])
    monkeypatch.setattr(classic.S, "disabled_skills", set())

    classic.cmd_new("review_1")
    classic.S.messages.append({"role": "user", "content": "keep this"})
    classic.cmd_skills("disable plan")
    classic.S.messages.clear()
    classic.S.disabled_skills.clear()
    classic.cmd_resume("review_1")

    assert classic.S.name == "review_1"
    assert classic.S.messages == [{"role": "user", "content": "keep this"}]
    assert "plan" in classic.S.disabled_skills


def test_compact_preserves_recent_messages(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(classic.S, "name", "")
    monkeypatch.setattr(classic.S, "messages", [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old response"},
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": "recent response"},
    ])
    response = type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": "The old request was completed."})(),
    })()]})()
    monkeypatch.setattr(classic.A, "create_completion", lambda *args, **kwargs: response)

    classic.cmd_compact("2")

    assert classic.S.messages[0]["role"] == "system"
    assert "old request was completed" in classic.S.messages[0]["content"]
    assert classic.S.messages[1:][-1]["content"] == "recent response"


def test_masthead_uses_one_line_mode_on_very_narrow_terminals(monkeypatch):
    monkeypatch.setattr(classic, "console", Console(file=io.StringIO(), width=54, color_system=None))

    assert classic._classic_masthead().plain == "AGENT8088"


def test_trace_save_exports_full_conversation(tmp_path, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(classic.S, "name", "trace_demo")
    monkeypatch.setattr(classic.S, "messages", [{"role": "user", "content": "hello"}])
    monkeypatch.setattr(classic.S, "conversation_trace", [])

    classic._record_trace("hello", [{"type": "final_answer", "content": "hi"}], 0.25)
    export_path = tmp_path / "conversation.json"
    classic.cmd_trace(f"save {export_path}")

    exported = json.loads(export_path.read_text())
    assert exported["messages"] == [{"role": "user", "content": "hello"}]
    assert exported["trace"][0]["input"] == "hello"
    assert "full conversation trace saved" in output.getvalue()


def test_trace_on_creates_and_updates_a_default_export(tmp_path, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setenv("AGENT8088_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(classic.S, "name", "")
    monkeypatch.setattr(classic.S, "messages", [{"role": "user", "content": "hello"}])
    monkeypatch.setattr(classic.S, "conversation_trace", [])
    monkeypatch.setattr(classic.S, "show_trace", False)
    monkeypatch.setattr(classic.S, "trace_path", "")

    classic.cmd_trace("on")
    classic._record_trace("hello", [{"type": "final_answer", "content": "hi"}], 0.25)

    export_path = classic.Path(classic.S.trace_path)
    exported = json.loads(export_path.read_text())
    assert export_path.parent == tmp_path
    assert exported["trace"][0]["input"] == "hello"
