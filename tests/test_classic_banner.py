import io
import json
import sys
from types import SimpleNamespace

import prompt_toolkit
from rich.console import Console

import agent8088.cli as classic


def test_classic_banner_includes_brand_and_catalogues(monkeypatch):
    output = io.StringIO()
    # legacy_windows=False pins which logo variant is under test. Left to the
    # ambient console, this asserts the block logo on Linux and the ASCII one on
    # a Windows terminal, so the same test passes or fails by platform rather
    # than by behaviour. The ASCII branch has its own test below.
    monkeypatch.setattr(classic, "console",
                        Console(file=output, width=180, color_system=None,
                                legacy_windows=False))

    classic.banner()

    rendered = output.getvalue()
    assert "AGENT8088" in rendered
    assert "█████╗  ██████╗" in rendered
    assert classic._PALINDROME_LOGO.is_file()
    logo = classic._palindrome_logo().plain
    assert "▀" in logo
    assert max(map(len, logo.splitlines())) == 24
    assert len(classic._classic_masthead().spans) == 6
    assert "Palindrome" in rendered
    assert "Research Labs" in rendered
    assert "Available Tools" in rendered
    assert "Available Skills" in rendered
    assert classic._catalog(["delta", "alpha"], columns=1) == "alpha\ndelta"


def test_logo_falls_back_to_ascii_on_a_legacy_console(monkeypatch):
    """A console that cannot render block characters must not emit them."""
    monkeypatch.setattr(classic, "console",
                        Console(file=io.StringIO(), width=180, color_system=None,
                                legacy_windows=True))
    logo = classic._palindrome_logo().plain
    assert "▀" not in logo and "▄" not in logo
    assert "#" in logo


def test_web_search_call_hides_the_query_in_the_cli(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=180, color_system=None))
    monkeypatch.setattr(classic.S, "verbose", "on")

    classic.on_calls([{"name": "web_search", "arguments": {"query": "private search terms"}}])

    rendered = output.getvalue()
    assert "Searching the web" in rendered
    assert "private search terms" not in rendered
    assert "web_search(" not in rendered


def test_palindrome_logo_falls_back_when_asset_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(classic, "_PALINDROME_LOGO", tmp_path / "missing.png")

    logo = classic._palindrome_logo().plain
    assert len(logo.splitlines()) == 8
    assert max(map(len, logo.splitlines())) <= 24


def test_palindrome_logo_uses_ascii_on_legacy_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(classic, "_PALINDROME_LOGO", tmp_path / "missing.png")
    monkeypatch.setattr(
        classic, "console",
        SimpleNamespace(legacy_windows=True, encoding="cp1252"),
    )

    logo = classic._palindrome_logo().plain

    assert "######" in logo
    assert all(ord(character) < 128 for character in logo)


def test_narrow_banner_keeps_the_palindrome_brand(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=50, color_system=None))

    classic.banner()

    rendered = output.getvalue()
    assert "Palindrome Research Labs" in rendered
    assert any(pixel in rendered for pixel in ("#", "█", "▀", "▄"))


def test_classic_masthead_compacts_on_narrow_terminals(monkeypatch):
    monkeypatch.setattr(classic, "console", Console(file=io.StringIO(), width=79, color_system=None))

    assert "/_\\ / __| __|" in classic._classic_masthead().plain
    monkeypatch.setattr(classic, "console", Console(file=io.StringIO(), width=80, color_system=None))
    assert "█████╗  ██████╗" in classic._classic_masthead().plain


def test_command_suggestions_cover_slash_and_bare_prefixes():
    assert "/help" in classic._command_matches("/")
    assert "/quit" in classic._command_matches("/")
    assert classic._command_matches("m", slash=False) == ["maxturns", "mcp", "mode", "model", "models"]
    assert classic._live_matches("/")[1] == classic._command_matches("/")
    assert classic._live_matches("/m")[1] == ["/maxturns", "/mcp", "/mode", "/model", "/models"]
    assert classic._live_matches("m") == ("m", ["maxturns", "mcp", "mode", "model", "models"])


def test_status_bar_summarizes_the_idle_session(monkeypatch):
    monkeypatch.setattr(classic, "_estimate_context_pct", lambda: 50)
    monkeypatch.setattr(classic, "_active_provider_name", lambda: "local")
    monkeypatch.setattr(classic.A, "MODEL_NAME", "test-model")
    monkeypatch.setattr(classic.A, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(classic.S, "name", "demo")
    monkeypatch.setattr(classic.S, "last_usage", {"seconds": 2.5, "tokens": 12})

    rendered = "".join(text for _, text in classic._status_bar_fragments())

    assert rendered == " ✢ local:test-model │ █████░░░░░ 50% ctx │ full-auto │ demo │ last 2.5s ↑12 │ ● idle "


def test_interactive_prompt_uses_the_persistent_status_bar(monkeypatch):
    captured = {}
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(prompt_toolkit, "prompt", lambda *args, **kwargs: captured.update(kwargs) or "hello")

    assert classic._read_line() == "hello"
    assert "idle" in "".join(text for _, text in captured["bottom_toolbar"]())


def test_default_skills_are_loaded_into_the_agent_and_status(monkeypatch):
    expected = {"plan", "systematic-debugging", "test-driven-development", "github-code-review", "documentation-writing"}
    assert expected <= set(classic.A.SKILL_PACKAGES)
    assert "## Installed skills" in classic.A.SYSTEM_PROMPT
    assert classic.A.SKILL_PACKAGES["plan"]["category"] == "workflow"

    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    classic.cmd_status("")
    assert "Session Status" in output.getvalue()


def test_plan_command_is_covered_by_the_plan_mode_suite():
    """`/plan` is a mode now, not a one-shot wrapper: see tests/test_plan_command.py.
    Kept as a signpost so the deletion of the old one-shot tests is deliberate —
    those tests asserted that /plan restored the previous mode when the turn ended,
    which is exactly the behaviour that made plan → approve → run impossible."""
    assert classic.cmd_plan.__doc__ and "plan mode" in classic.cmd_plan.__doc__


def test_named_session_round_trips_skill_state(tmp_path, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(classic, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(classic.A, "CONFIG_PATH", tmp_path / "config.txt")
    monkeypatch.setattr(classic.A, "APP_CONFIG", {})
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
    monkeypatch.setattr(classic, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(classic.S, "name", "trace_demo")
    monkeypatch.setattr(classic.S, "messages", [{"role": "user", "content": "hello"}])
    monkeypatch.setattr(classic.S, "conversation_trace", [])
    monkeypatch.setattr(classic.A, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(classic.A, "NO_PROMPT_PATHS", [tmp_path])

    classic._record_trace("hello", [{"type": "final_answer", "content": "hi"}], 0.25)
    export_path = tmp_path / "conversation.json"
    classic.cmd_trace(f"save {export_path}")

    exported = json.loads(export_path.read_text())
    assert exported["messages"] == [{"role": "user", "content": "hello"}]
    assert exported["trace"][0]["input"] == "hello"
    if sys.platform != "win32":
        assert export_path.stat().st_mode & 0o777 == 0o600
    assert "full conversation trace saved" in output.getvalue()


def test_save_export_uses_private_permission_gated_write(tmp_path, monkeypatch):
    output = io.StringIO()
    target = tmp_path / "conversation.json"
    seen = {}
    real_run_tool = classic.A.run_tool
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(classic.S, "messages", [{"role": "user", "content": "private"}])
    monkeypatch.setattr(classic.S, "conversation_trace", [])
    monkeypatch.setattr(classic.S, "last_trace", None)
    monkeypatch.setattr(classic.S, "name", "")
    monkeypatch.setattr(classic.S, "disabled_skills", set())
    monkeypatch.setattr(classic.A, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(classic.A, "NO_PROMPT_PATHS", [tmp_path])
    monkeypatch.setattr(classic.A, "PERMISSION_MODE", "readonly")

    def capture_run_tool(name, args, *call_args, **call_kwargs):
        seen.update(args)
        return real_run_tool(name, args, *call_args, **call_kwargs)

    monkeypatch.setattr(classic.A, "run_tool", capture_run_tool)

    classic.cmd_save(str(target))

    assert seen["_private"] is True
    assert json.loads(target.read_text())["messages"] == [
        {"role": "user", "content": "private"},
    ]
    if sys.platform != "win32":
        assert target.stat().st_mode & 0o777 == 0o600
    assert "saved" in output.getvalue()


def test_trace_save_respects_write_permissions(tmp_path, monkeypatch):
    output = io.StringIO()
    target = tmp_path / "blocked.json"
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(classic.A, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(classic.A, "PROMPT_PATHS", [tmp_path])
    monkeypatch.setattr(classic.A, "NO_PROMPT_PATHS", [])
    monkeypatch.setattr(classic.A, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(classic, "_handle_escalation", lambda _: False)

    classic.cmd_trace(f"save {target}")

    assert not target.exists()
    assert "could not save" in output.getvalue()


def test_trace_on_creates_and_updates_a_default_export(tmp_path, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setenv("AGENT8088_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(classic.A, "CONFIG_PATH", tmp_path / "config.txt")
    monkeypatch.setattr(classic.A, "APP_CONFIG", {})
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


def test_preferences_persist_across_launches(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    output = io.StringIO()
    monkeypatch.setattr(classic, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(classic.A, "CONFIG_PATH", config)
    monkeypatch.setattr(classic.A, "APP_CONFIG", {})
    monkeypatch.setenv("AGENT8088_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setattr(classic.S, "name", "")
    monkeypatch.setattr(classic.S, "temperature", 0.1)
    monkeypatch.setattr(classic.S, "max_turns", 10)
    monkeypatch.setattr(classic.S, "show_trace", False)
    monkeypatch.setattr(classic.S, "show_reasoning", False)
    monkeypatch.setattr(classic.S, "verbose", "on")
    monkeypatch.setattr(classic.S, "usage_mode", "tokens")
    monkeypatch.setattr(classic.S, "disabled_skills", set())
    monkeypatch.setattr(classic.S, "trace_path", "")

    assert classic.Session().show_trace is False
    classic.cmd_verbose("full")
    assert classic.S.show_trace is True
    assert classic.Path(classic.S.trace_path).is_file()
    classic.cmd_trace("off")
    classic.cmd_trace("on")
    classic.cmd_reasoning("on")
    classic.cmd_usage("full")
    classic.cmd_temp("0.35")
    classic.cmd_maxturns("14")
    classic.cmd_skills("disable plan")

    monkeypatch.setattr(classic.A, "APP_CONFIG", classic.A.load_simple_config(config))
    restored = classic.Session()
    assert restored.temperature == 0.35
    assert restored.max_turns == 14
    assert restored.show_trace is True
    assert restored.show_reasoning is True
    assert restored.verbose == "full"
    assert restored.usage_mode == "full"
    assert restored.disabled_skills == {"plan"}

    monkeypatch.setattr(classic, "S", restored)
    monkeypatch.setattr(sys, "argv", ["agent8088"])
    monkeypatch.setattr(classic, "_install_completion", lambda: None)
    monkeypatch.setattr(classic, "banner", lambda: None)
    monkeypatch.setattr(classic, "_read_line", lambda: "exit")
    classic.main()

    assert restored.trace_path
    assert classic.Path(restored.trace_path).is_file()

    classic.cmd_trace("off")
    monkeypatch.setattr(classic.A, "APP_CONFIG", classic.A.load_simple_config(config))
    assert classic.Session().show_trace is False
