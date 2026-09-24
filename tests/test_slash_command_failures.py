"""Regressions from the 2026-09-24 slash-command test pass (CLI + web UI)."""

import io
import urllib.error

import pytest
from rich.console import Console

from agent8088 import cli, local_models
from agent8088 import engine as A


def _capture(fn, *args):
    console = Console(file=io.StringIO(), width=200, force_terminal=False, legacy_windows=False)
    original = cli.console
    cli.console = console
    try:
        result = fn(*args)
    finally:
        cli.console = original
    return result, console.file.getvalue()


@pytest.mark.parametrize("reply", [
    "Unsupported Windows schedule (weekday).",   # /schedule add ... 1-5 on Windows
    "No matching scheduled task.",               # /schedule remove <never scheduled>
])
def test_schedule_failures_are_not_reported_as_success(monkeypatch, reply):
    monkeypatch.setattr(A, "_exec_cron", lambda _args: reply)
    result = A.schedule_task(action="add", schedule="30 18 * * 1-5", task="x")
    assert result["ok"] is False
    assert result["detail"] == reply


def test_local_remove_404_says_model_missing_not_daemon_down(monkeypatch):
    def refuse(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error":"model not found"}'))
    monkeypatch.setattr(local_models.urllib.request, "urlopen", refuse)
    with pytest.raises(local_models.OllamaError) as err:
        local_models.remove_model("nosuchmodel")
    assert "not installed" in str(err.value)
    assert "isn't running" not in str(err.value)


def test_web_destructive_command_asks_for_yes_instead_of_reading_stdin(monkeypatch):
    monkeypatch.setattr(A, "DESTRUCTIVE_CONFIRM", True)
    monkeypatch.setattr(cli, "WEB_CONFIRM", {"command": "/memory clear", "yes": False})
    ok, out = _capture(cli._confirm_destructive, "Delete all 2 memories")
    assert ok is False
    assert "/memory clear --yes" in out

    monkeypatch.setattr(cli, "WEB_CONFIRM", {"command": "/memory clear", "yes": True})
    ok, _ = _capture(cli._confirm_destructive, "Delete all 2 memories")
    assert ok is True


@pytest.mark.parametrize("fn,arg,hint", [
    (cli.cmd_usage, "bogus", "[off|tokens|full]"),
    (cli.cmd_compact, "1", "[keep>=2]"),
    (cli.cmd_verbose, "bogus", "[on|off|full]"),
])
def test_usage_hints_are_not_eaten_as_rich_markup(fn, arg, hint):
    _, out = _capture(fn, arg)
    assert hint in out


def test_skills_enable_without_a_name_prints_usage():
    _, out = _capture(cli.cmd_skills, "enable")
    assert "usage:" in out and "/skills enable <name>" in out


def test_select_agent_without_termios_uses_the_choice_picker(monkeypatch):
    monkeypatch.setattr(cli, "termios", None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli, "_choice_prompt", lambda message, choices, default="": choices[1])
    profiles = {"coder": {}, "explore": {}}
    assert cli.select_agent(profiles) == "explore"


@pytest.mark.parametrize("provider", ["openai", "custom", "default", "gemma", "ornith"])
def test_model_switch_never_says_unknown_provider_for_known_names(monkeypatch, provider):
    """A legacy-alias note was inserted between an if/elif chain's branches,
    which split the chain: every bare /model <name> printed "unknown provider"
    (and an unknown one still must), while the switch had already happened."""
    monkeypatch.setattr(A, "PROVIDERS", {"openai": {"model": "gpt-4o", "api_mode": "openai",
                                                    "base_url": "http://x"}})
    monkeypatch.setattr(A, "MODEL_NAME", "gpt-4o")
    monkeypatch.setattr(A, "DEFAULT_PROVIDER", "")
    monkeypatch.delenv("USE_GEMMA4", raising=False)
    monkeypatch.setattr(A, "get_client", lambda *a, **k: ("client", "gpt-4o"))
    monkeypatch.setattr(A, "activate_model", lambda *a, **k: None)
    monkeypatch.setattr(A, "_active_model_token_limits", lambda: (32768, 8192))
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "openai")
    monkeypatch.setattr(cli, "banner", lambda: None)
    _, out = _capture(cli.cmd_model, provider)
    assert "unknown provider" not in out
    assert "switched" in out


def test_model_switch_still_rejects_an_unknown_provider(monkeypatch):
    monkeypatch.setattr(A, "PROVIDERS", {"openai": {"model": "gpt-4o", "api_mode": "openai",
                                                    "base_url": "http://x"}})
    monkeypatch.setattr(A, "_active_model_token_limits", lambda: (32768, 8192))
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "openai")
    monkeypatch.setattr(cli, "banner", lambda: None)
    _, out = _capture(cli.cmd_model, "bogus")
    assert "unknown provider" in out
