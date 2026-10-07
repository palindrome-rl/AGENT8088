"""Small display fixes: what the user sees should not carry framing meant for
the model, and /doctor must not cut off the path that names the problem."""
import json
import os
import sys
from pathlib import Path

import pytest

from agent8088 import cli, web_server

WRAPPED = ('<<<EXTERNAL_UNTRUSTED_CONTENT source="shell command: pwd && ls">>>\n'
           "/work/space\ndata.csv\n<<<END_UNTRUSTED_CONTENT>>>")


def _printed(monkeypatch, name, result):
    monkeypatch.setattr(cli.S, "verbose", "on")
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: printed.append(a[0]))
    cli.on_result(name, result)
    return "\n".join(getattr(item, "plain", str(item)) for item in printed)


# --- 1. untrusted-content markers stay out of the user's view ---------------

def test_shell_preview_hides_the_untrusted_markers(monkeypatch):
    text = _printed(monkeypatch, "execute_shell", WRAPPED)
    assert "EXTERNAL_UNTRUSTED" not in text and "END_UNTRUSTED" not in text
    assert "/work/space" in text and "data.csv" in text


def test_unlabelled_marker_and_trailing_note_are_handled(monkeypatch):
    raw = ("<<<EXTERNAL_UNTRUSTED_CONTENT>>>\nhello\n<<<END_UNTRUSTED_CONTENT>>>\n"
           "[agent8088] Commands now run in /work.")
    text = _printed(monkeypatch, "mcp_tool", raw)
    assert "UNTRUSTED" not in text
    assert "hello" in text and "Commands now run in /work." in text


def test_web_payload_hides_the_untrusted_markers():
    payload = web_server._tool_result_payload("execute_shell", WRAPPED)
    assert "UNTRUSTED" not in payload["result"]
    assert "data.csv" in payload["result"]


def test_the_model_still_gets_the_markers(engine):
    # Display only: the wrapped text the model reads is unchanged.
    wrapped = engine._wrap_untrusted("data.csv", "shell command: ls")
    assert wrapped.startswith("<<<EXTERNAL_UNTRUSTED_CONTENT")
    assert engine.strip_untrusted_markers(wrapped) == "data.csv"


# --- 2. tool errors read as a sentence, not a JSON blob ----------------------

ERROR = ('Error: The working directory /app does not exist, so no command can start.\n'
         '{"code": "working_directory_missing", "recoverable": false, '
         '"suggested_action": "Tell the user to set shell_cwd."}')


def test_error_shows_the_message_and_code_not_the_json(monkeypatch):
    text = _printed(monkeypatch, "execute_shell", ERROR)
    assert "The working directory /app does not exist, so no command can start." in text
    assert "working_directory_missing" in text
    assert "{" not in text and "suggested_action" not in text and "recoverable" not in text


def test_error_keeps_a_note_that_follows_the_json(monkeypatch):
    text = _printed(monkeypatch, "execute_shell",
                    ERROR + "\n[agent8088] Command timed out twice; try a smaller step.")
    assert "Command timed out twice" in text
    assert "suggested_action" not in text


def test_unstructured_error_is_shown_as_before(monkeypatch):
    text = _printed(monkeypatch, "execute_shell", "Error: something odd {not json}")
    assert "Error: something odd {not json}" in text


# --- 3. read-only command chains are not recorded as changes ----------------

@pytest.mark.parametrize("command", [
    "pwd && ls -la", "ls; pwd", "cat a.txt || ls", "ls | wc -l && pwd",
])
def test_read_only_chains_change_nothing(engine, command):
    assert engine._readonly_chain(command)


@pytest.mark.parametrize("command", [
    "pwd && rm -rf x", "ls; touch f", "ls & rm x", "ls && echo hi > f",
    "ls && $(rm x)", "ls && `rm x`", "ls\nrm x", "pwd", "",
])
def test_anything_else_is_not_a_read_only_chain(engine, command):
    assert not engine._readonly_chain(command)


def test_permission_check_is_unchanged_for_chains(engine):
    # Readonly mode still escalates a chain: only bookkeeping learns chains.
    assert not engine._readonly_shell("pwd && ls -la")


posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX shell commands")


@posix_only
def test_read_only_chain_does_not_bump_the_mutation_counter(engine, tmp_path, monkeypatch):
    engine.TOOL_SPECS["execute_shell"]["host"] = "1"
    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    before = engine._MUTATION_SEQ
    engine.exec_tool("execute_shell", json.dumps({"command": "pwd && ls"}))
    assert engine._MUTATION_SEQ == before
    engine.exec_tool("execute_shell", json.dumps({"command": "pwd && touch made.txt"}))
    assert engine._MUTATION_SEQ == before + 1


# --- 4. /doctor shows whole paths -------------------------------------------

def _doctor_lines(monkeypatch, checks, width=60):
    from rich.console import Console
    out = Console(width=width, record=True, force_terminal=False)
    monkeypatch.setattr(cli, "console", out)
    cli._render_doctor(checks)
    return out.export_text()


def test_doctor_wraps_a_long_path_instead_of_cutting_it(monkeypatch):
    path = "/srv/" + "/".join(f"segment{i:02d}" for i in range(12)) + "/the-missing-folder"
    text = _doctor_lines(monkeypatch, [cli._doctor_check(
        "Working directory", "warn", f"shell_cwd {path} does not exist")])
    assert "…" not in text
    assert "the-missing-folder" in text.replace("\n", "").replace(" ", "")


def test_doctor_shows_the_home_folder_as_tilde(monkeypatch):
    home = str(Path.home())
    text = _doctor_lines(monkeypatch, [cli._doctor_check(
        "Configuration", "ok", f"{home}/.agent8088/config.txt (found)")], width=120)
    assert "~/.agent8088/config.txt" in text
    assert home + "/" not in text


def test_tilde_only_replaces_whole_path_segments(monkeypatch):
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: Path("/Users/al")))
    assert cli._tilde("/Users/al/x and /Users/alice/y and /Users/al") == \
        "~/x and /Users/alice/y and ~"


# --- web search and answers: markers and the model-only date note ------------

SEARCH = ("[Retrieved 2026-10-07. Check each result's own date before calling "
          "anything current, latest, or upcoming — search results routinely "
          "include older pages.]\n\n"
          '<<<EXTERNAL_UNTRUSTED_CONTENT source="web_search:ddgs">>>\n'
          "Search results (via ddgs):\n\n1. Example\n   https://example.com\n"
          "<<<END_UNTRUSTED_CONTENT>>>")


def test_web_payload_for_search_drops_markers_and_date_note():
    shown = web_server._tool_result_payload("web_search", SEARCH)["result"]
    assert "UNTRUSTED" not in shown and "Retrieved" not in shown
    assert shown.startswith("Search results (via ddgs):") and "https://example.com" in shown


def test_date_note_is_kept_for_other_tools(engine):
    text = "[Retrieved by hand] notes"
    assert engine.display_tool_result("read_text", text) == text


def test_answer_that_copies_the_markers_is_cleaned(engine):
    answer = ('Here is what I found:\n<<<EXTERNAL_UNTRUSTED_CONTENT source="web_search:ddgs">>>\n'
              "Rodri plays for Manchester City.\n<<<END_UNTRUSTED_CONTENT>>>")
    cleaned = engine._guard_answer(answer)
    assert "UNTRUSTED" not in cleaned
    assert "Rodri plays for Manchester City." in cleaned


def _streamed(chunks):
    stream = cli._StreamFilter()
    shown = []
    for chunk in chunks:
        stream.feed(chunk)
        shown.append(stream.prose_text())
    return shown


def test_stream_never_shows_a_marker_even_mid_tag():
    shown = _streamed(["Found it. ", "<<<EXTERN", 'AL_UNTRUSTED_CONTENT source="web_search:ddgs">>>\n',
                       "Rodri plays for City.", "\n<<<END_UNTR", "USTED_CONTENT>>>", " Done."])
    assert all("<<<" not in view and "UNTR" not in view for view in shown)
    assert shown[-1] == "Found it. Rodri plays for City. Done."


def test_stream_releases_ordinary_angle_brackets():
    shown = _streamed(["if a <", "< b then", " use <<<heredoc>>> here"])
    assert shown[-1] == "if a << b then use <<<heredoc>>> here"


# --- /help fits a normal terminal -------------------------------------------

@pytest.mark.parametrize("width", [80, 100, 120])
def test_help_shows_every_description_at_common_widths(monkeypatch, width):
    from rich.console import Console
    out = Console(width=width, record=True, force_terminal=False, file=open(os.devnull, "w"))
    monkeypatch.setattr(cli, "console", out)
    cli.cmd_help("")
    text = out.export_text()
    flat = " ".join(text.split())
    for command in cli.command_catalog():
        first_words = " ".join(command["description"].split()[:2])
        assert first_words in flat, f"{command['usage']}: description missing at {width} cols"
    # Descriptions get the room: no row squeezed to a sliver of one character.
    assert "W…" not in text
    assert len(text.splitlines()) < 260


# --- start-up says when the provider's API key is missing --------------------

def _quiet_other_notices(monkeypatch):
    monkeypatch.setattr(cli.A, "CONFIG_WARNINGS", [])
    from agent8088 import memory
    monkeypatch.setattr(memory, "memory_status", lambda: {"engine": "native", "ok": True,
                                                          "error": "", "fix": ""})
    monkeypatch.setattr(cli, "_mcp_failure_notice", lambda: [])


def test_startup_notices_name_a_missing_api_key(monkeypatch):
    _quiet_other_notices(monkeypatch)
    monkeypatch.setitem(cli.A.PROVIDERS, "ollama-cloud", {"api_key_env": "OLLAMA_API_KEY",
                                                          "base_url": "https://ollama.com/v1"})
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "ollama-cloud")
    monkeypatch.setattr(cli.A, "_provider_api_key", lambda profile: "")
    assert cli._startup_notices() == [
        "OLLAMA_API_KEY isn't set — set it or run `agent8088 --setup`."]


def test_startup_notices_are_quiet_when_the_key_is_set_or_not_needed(monkeypatch):
    _quiet_other_notices(monkeypatch)
    monkeypatch.setitem(cli.A.PROVIDERS, "ollama-cloud", {"api_key_env": "OLLAMA_API_KEY",
                                                          "base_url": "https://ollama.com/v1"})
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "ollama-cloud")
    monkeypatch.setattr(cli.A, "_provider_api_key", lambda profile: "set")
    assert cli._startup_notices() == []
    monkeypatch.setitem(cli.A.PROVIDERS, "ollama", {"base_url": "http://localhost:11434/v1"})
    monkeypatch.setattr(cli, "_active_provider_name", lambda: "ollama")
    monkeypatch.setattr(cli.A, "_provider_api_key", lambda profile: "")
    assert cli._startup_notices() == []


# --- tables never cut off a name the user types back -------------------------

def _render(monkeypatch, fn, width=80):
    from rich.console import Console
    out = Console(width=width, record=True, force_terminal=False, file=open(os.devnull, "w"))
    monkeypatch.setattr(cli, "console", out)
    fn()
    return out.export_text()


def test_tools_table_shows_whole_tool_names(monkeypatch):
    text = _render(monkeypatch, cli._render_tools_table)
    for name in cli._active_tool_specs():
        assert name in text, f"{name} cut off in /tools at 80 cols"
    assert "…" not in text
    assert len(text.splitlines()) < 5 * len(cli._active_tool_specs())


@pytest.mark.parametrize("width", [120, 160])
def test_wide_tool_and_skill_tables_keep_whole_names(monkeypatch, width):
    text = _render(monkeypatch, cli._render_tools_table, width=width)
    assert "Mode" in text  # the table layout
    for name in cli._active_tool_specs():
        assert name in text
    text = _render(monkeypatch, lambda: cli.cmd_skills(""), width=width)
    for name in cli.A.SKILL_PACKAGES:
        assert name in text


def test_skills_table_shows_whole_names_and_one_line_descriptions(monkeypatch):
    if not cli.A.SKILL_PACKAGES:
        pytest.skip("no skills installed")
    text = _render(monkeypatch, lambda: cli.cmd_skills(""))
    for name in cli.A.SKILL_PACKAGES:
        assert name in text, f"{name} cut off in /skills at 80 cols"
    # One short description per skill, not every full playbook summary.
    assert len(text.splitlines()) < 6 * len(cli.A.SKILL_PACKAGES)
    assert "…" not in text


def test_limits_table_shows_whole_setting_names(monkeypatch):
    text = _render(monkeypatch, cli._show_limits)
    for key in cli.A.LIMIT_SPECS:
        assert key in text, f"{key} cut off in /limits at 80 cols"


def test_agents_table_shows_whole_tool_names(monkeypatch):
    monkeypatch.setattr(cli, "_fetch_models_for_provider", lambda provider: [])
    text = _render(monkeypatch, cli._cmd_agents_list)
    flat = "".join(text.split())  # a folded name is still whole, split over lines
    for name in cli.A.SUBAGENT_SPECS:
        assert name in text
        for tool in cli.A.SUBAGENT_SPECS[name]["tools"]:
            if tool in cli.A.TOOL_NAMES:
                assert tool in flat, f"{tool} cut off in /agents at 80 cols"
    assert "…" not in text


# --- a bare toggle shows its setting instead of flipping it -----------------

def _quiet(monkeypatch):
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else ""))
    saved = []
    monkeypatch.setattr(cli, "_save_preferences", lambda: saved.append(True))
    return printed, saved


@pytest.mark.parametrize("arg", ["", "status", "bogus"])
def test_bare_or_unknown_trace_does_not_flip_it(monkeypatch, arg):
    printed, saved = _quiet(monkeypatch)
    monkeypatch.setattr(cli.S, "show_trace", False)
    cli.cmd_trace(arg)
    assert cli.S.show_trace is False and not saved
    assert "off" in " ".join(printed)


@pytest.mark.parametrize("arg", ["", "status", "bogus"])
def test_bare_or_unknown_reasoning_does_not_flip_it(monkeypatch, arg):
    printed, saved = _quiet(monkeypatch)
    monkeypatch.setattr(cli.S, "show_reasoning", True)
    cli.cmd_reasoning(arg)
    assert cli.S.show_reasoning is True and not saved
    assert "on" in " ".join(printed)


def test_bare_verbose_does_not_reset_it(monkeypatch):
    printed, saved = _quiet(monkeypatch)
    monkeypatch.setattr(cli.S, "verbose", "full")
    cli.cmd_verbose("")
    assert cli.S.verbose == "full" and not saved
    assert "full" in " ".join(printed)


def test_explicit_toggles_still_work(monkeypatch, tmp_path):
    _quiet(monkeypatch)
    monkeypatch.setattr(cli.S, "show_reasoning", False)
    cli.cmd_reasoning("on")
    assert cli.S.show_reasoning is True
    monkeypatch.setattr(cli.S, "show_trace", True)
    cli.cmd_trace("off")
    assert cli.S.show_trace is False
    monkeypatch.setattr(cli.S, "verbose", "on")
    cli.cmd_verbose("off")
    assert cli.S.verbose == "off"


def test_agents_table_on_a_wide_terminal_keeps_names_and_tools_whole(monkeypatch):
    monkeypatch.setattr(cli, "_fetch_models_for_provider", lambda provider: [])
    text = _render(monkeypatch, cli._cmd_agents_list, width=140)
    assert "Max turns" in text  # the table layout, not the narrow blocks
    for name, spec in cli.A.SUBAGENT_SPECS.items():
        assert name in text
        for tool in spec["tools"]:
            if tool in cli.A.TOOL_NAMES:
                assert tool in text


def test_toggle_status_keeps_bracketed_options(monkeypatch):
    from rich.console import Console
    out = Console(width=120, record=True, force_terminal=False, file=open(os.devnull, "w"))
    monkeypatch.setattr(cli, "console", out)
    monkeypatch.setattr(cli.S, "show_trace", False)
    cli.cmd_trace("bogus")
    text = out.export_text()
    assert "/trace save [file]" in text and "/trace on|off|save [file]" in text
