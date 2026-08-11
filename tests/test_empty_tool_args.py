"""A call that arrives with no usable arguments must say so usefully.

A model emitted `✿FUNCTION✿: execute_shell` with no ✿ARGS✿ block. The parser
allows argument-less calls (git_status needs none), so the tool was reached with
arguments={} and `_format_with_args` raised "Missing required argument: command".
That is true and useless: it reads as "the argument you sent is named wrong", so
the model re-sent the identical shape and gave up after the second try.

This is the mirror of the case `_tool_arg_parse_error` already covers — telling a
model an argument is absent when it did send one. The reverse needs the same
care: say that the ✿ARGS✿ block itself is missing, and show what one looks like.
"""
FUNC = "✿FUNCTION✿"
ARGS = "✿ARGS✿"


def _calls(engine, text):
    return engine.find_tool_calls(text, set(engine.TOOL_SPECS))


# --- the reported bug -------------------------------------------------------

def test_a_shell_call_with_no_arguments_is_answered_not_raised(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")

    out = engine.run_tool("execute_shell", {})

    assert out.startswith("Error:"), "an empty call must be answered, not raised"


def test_the_error_says_the_args_block_is_what_is_missing(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")

    out = engine.run_tool("execute_shell", {})

    assert ARGS in out, "naming the parameter alone is what sent the model in circles"
    assert "execute_shell" in out
    assert "command" in out


def test_the_error_shows_a_usable_example(engine, monkeypatch):
    """A shape it can copy beats a description of the shape."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")

    out = engine.run_tool("execute_shell", {})

    assert '{"command"' in out


def test_a_bare_function_line_reaches_that_error(engine, monkeypatch):
    """End to end, the way the model actually emitted it."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    call = _calls(engine, f"{FUNC}: execute_shell")[0]

    out = engine.run_tool(call["name"], call["arguments"])

    assert ARGS in out


def test_an_empty_args_object_reaches_that_error_too(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    call = _calls(engine, f"{FUNC}: execute_shell {ARGS}: {{}}")[0]

    out = engine.run_tool(call["name"], call["arguments"])

    assert ARGS in out


# --- an ARGS block that is not JSON must not vanish -------------------------

def test_a_non_json_args_block_still_produces_a_call(engine):
    """The header regex requires a '{', so this matched nothing and was dropped.

    The model's call disappeared silently and it saw no result at all — worse
    than a wrong answer, because there is nothing to react to.
    """
    calls = _calls(engine, f"{FUNC}: execute_shell {ARGS}: head -5 library.py")

    assert calls, "the call must not be silently dropped"
    assert calls[0]["name"] == "execute_shell"


def test_a_non_json_args_block_is_reported_as_a_parse_failure(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    call = _calls(engine, f"{FUNC}: execute_shell {ARGS}: head -5 library.py")[0]

    out = engine.run_tool(call["name"], call["arguments"])

    assert "could not parse" in out
    assert "head -5 library.py" in out, "quote what arrived, so the model can see it"


# --- what must keep working -------------------------------------------------

def test_a_tool_that_needs_no_arguments_is_unaffected(engine, monkeypatch):
    """git_status has no placeholder to fill, so an empty call is correct."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_exec_process", lambda *a, **k: "## main")
    monkeypatch.setattr(engine, "_exec_sandbox_command", lambda *a, **k: "## main")

    out = engine.run_tool("git_status", {})

    assert ARGS not in out


def test_schedule_task_with_no_arguments_still_reaches_the_tool(engine, monkeypatch):
    """The constraint that rules out a blanket "declares args, got none" rule.

    schedule_task declares action/schedule/task, but _exec_cron defaults action
    to "list", so an argument-less call is a legitimate way to list schedules.
    """
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_exec_cron", lambda args: "listed")

    assert engine.run_tool("schedule_task", {}) == "listed"


def test_a_well_formed_call_is_untouched(engine):
    args = _calls(engine, f'{FUNC}: execute_shell {ARGS}: {{"command": "ls"}}')[0]["arguments"]

    assert args == {"command": "ls"}
