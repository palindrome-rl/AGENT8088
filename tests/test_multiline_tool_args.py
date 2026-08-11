"""Multi-line tool arguments must survive parsing.

Models naturally emit real newlines inside JSON string values when writing code
or file content. A literal newline inside a JSON string is invalid JSON, so
json.loads raised, the exception was swallowed, and find_tool_calls fell through
to the "loose FUNCTION line with no args" branch — producing arguments={}. The
tool then reported the argument as missing even though the model had sent it
("sandboxed execution requires 'code'"), which is actively misleading.
"""

FUNC = "\u273fFUNCTION\u273f"
ARGS = "\u273fARGS\u273f"


def _call(engine, text):
    calls = engine.find_tool_calls(text, set(engine.TOOL_SPECS))
    return calls[0]["arguments"] if calls else None


# --- the reported bug -------------------------------------------------------

def test_run_sandboxed_multiline_code_parses(engine):
    args = _call(engine, f'{FUNC}: run_sandboxed {ARGS}: {{"code": "a = 4\nb = 9999995\nprint(a + b)"}}')
    assert args, "no tool call parsed at all"
    assert "a = 4" in args.get("code", "")
    assert "print(a + b)" in args["code"]


def test_write_file_multiline_content_parses(engine):
    args = _call(engine, f'{FUNC}: write_file {ARGS}: {{"filename": "/tmp/a.py", "content": "def f():\n    return 1"}}')
    assert args and args.get("filename") == "/tmp/a.py"
    assert args["content"] == "def f():\n    return 1"


def test_execute_shell_multiline_command_parses(engine):
    args = _call(engine, f'{FUNC}: execute_shell {ARGS}: {{"command": "echo a\necho b"}}')
    assert args and args.get("command") == "echo a\necho b"


def test_tabs_and_carriage_returns_survive(engine):
    args = _call(engine, f'{FUNC}: run_sandboxed {ARGS}: {{"code": "if x:\n\tprint(1)\r\n"}}')
    assert args and "\t" in args.get("code", "")


def test_already_escaped_newlines_still_work(engine):
    """The repair must not double-escape valid JSON."""
    args = _call(engine, f'{FUNC}: run_sandboxed {ARGS}: {{"code": "a=1\\nb=2"}}')
    assert args and args["code"] == "a=1\nb=2"


def test_unescaped_windows_path_is_repaired(engine):
    args = _call(engine, f'{FUNC}: write_file {ARGS}: '
                 '{"filename": "C:\\Users\\Admin\\out.txt", "content": "ok"}')
    assert args and args["filename"] == r"C:\Users\Admin\out.txt"


def test_escaped_quote_inside_multiline_string(engine):
    args = _call(engine, f'{FUNC}: run_sandboxed {ARGS}: {{"code": "print(\\"hi\\")\nprint(2)"}}')
    assert args and 'print("hi")' in args["code"]
    assert "print(2)" in args["code"]


def test_backslash_before_quote_is_not_miscounted(engine):
    r"""A literal backslash (\\) must not be read as escaping the next quote."""
    args = _call(engine, f'{FUNC}: run_sandboxed {ARGS}: {{"code": "print(\\"a\\\\\\\\\\")\nx=1"}}')
    assert args is not None and "code" in args


# --- an unparseable ARGS block must not masquerade as "no args" -------------

def test_unparseable_args_reports_a_parse_error_not_a_missing_arg(engine):
    args = _call(engine, f'{FUNC}: run_sandboxed {ARGS}: {{"code": not valid json at all}}')
    assert args is not None, "should still surface a call so the model gets feedback"
    assert args.get("__parse_error__"), "must flag the parse failure, not look like empty args"


def test_parse_error_produces_an_actionable_message(engine):
    engine.PERMISSION_MODE = "full-auto"
    out = engine.run_tool("run_sandboxed", {"__parse_error__": '{"code": oops}'})
    assert "could not parse" in out.lower()
    assert "\\n" in out, "should tell the model how to escape newlines"
    assert "requires 'code'" not in out, "must not claim the arg is missing"


def test_genuinely_missing_arg_still_says_missing(engine):
    """The old message is still correct when the model really sent nothing."""
    engine.PERMISSION_MODE = "full-auto"
    assert "requires 'code'" in engine.run_tool("run_sandboxed", {})


def test_no_args_block_still_falls_through_to_empty(engine):
    """A bare FUNCTION line with no ARGS at all keeps its old behaviour."""
    args = _call(engine, f"{FUNC}: last_output")
    assert args == {}


# --- run_sandboxed argument aliases ----------------------------------------

def test_run_sandboxed_accepts_common_arg_aliases(engine, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(engine, "_exec_sandbox_command", lambda code, **_: code)
    for alias in ("script", "python", "source", "snippet", "command"):
        out = engine.run_tool("run_sandboxed", {alias: "print(4 + 9999995)"})
        assert out == "print(4 + 9999995)", f"alias {alias!r} not accepted: {out[:120]}"


def test_run_sandboxed_strips_markdown_fences(engine, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(engine, "_exec_sandbox_command", lambda code, **_: code)
    out = engine.run_tool("run_sandboxed", {"code": "```python\nprint(4 + 9999995)\n```"})
    assert out == "print(4 + 9999995)"


def test_run_sandboxed_strips_bare_fences(engine, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(engine, "_exec_sandbox_command", lambda code, **_: code)
    out = engine.run_tool("run_sandboxed", {"code": "```\nprint(1 + 1)\n```"})
    assert out == "print(1 + 1)"


def test_code_takes_precedence_over_aliases(engine, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(engine, "_exec_sandbox_command", lambda code, **_: code)
    out = engine.run_tool("run_sandboxed", {"code": "print(111)", "script": "print(222)"})
    assert "111" in out and "222" not in out


# --- several calls in one message -------------------------------------------
# Pattern 2 used one re.search with a greedy (\{.*\}). Greedy was deliberate — a
# non-greedy match truncates nested JSON like {"steps": "[{\"tool\": ...}]"} — but
# with three FUNCTION/ARGS blocks in one reply it spans from the first brace to
# the last, so all three calls arrive as one unparseable blob and every one of
# them is lost. Models batch calls exactly like this when executing an approved
# plan step by step.

def _calls(engine, text):
    return engine.find_tool_calls(text, set(engine.TOOL_SPECS))


def test_three_batched_calls_all_parse(engine):
    text = "\n".join(
        f'{FUNC}: write_file {ARGS}: {{"filename": "{name}.txt", "content": "{name}"}}'
        for name in ("a", "b", "c"))

    calls = _calls(engine, text)

    assert [c["name"] for c in calls] == ["write_file"] * 3
    assert [c["arguments"]["filename"] for c in calls] == ["a.txt", "b.txt", "c.txt"]
    assert [c["arguments"]["content"] for c in calls] == ["a", "b", "c"]


def test_batched_calls_may_name_different_tools(engine):
    text = (f'{FUNC}: write_file {ARGS}: {{"filename": "a.txt", "content": "a"}}\n'
            f'{FUNC}: read_text {ARGS}: {{"filename": "a.txt"}}')

    assert [c["name"] for c in _calls(engine, text)] == ["write_file", "read_text"]


def test_nested_json_in_a_batch_is_not_truncated(engine):
    """The reason the greedy match existed: a step array inside a string value."""
    steps = '[{\\"tool\\": \\"write_file\\", \\"arguments\\": {\\"filename\\": \\"a.txt\\"}}]'
    text = (f'{FUNC}: execute_plan {ARGS}: {{"steps": "{steps}"}}\n'
            f'{FUNC}: read_text {ARGS}: {{"filename": "a.txt"}}')

    calls = _calls(engine, text)

    assert [c["name"] for c in calls] == ["execute_plan", "read_text"]
    assert calls[0]["arguments"]["steps"].endswith("}}]")
    assert calls[1]["arguments"] == {"filename": "a.txt"}


def test_one_unparseable_block_does_not_lose_the_others(engine):
    text = (f'{FUNC}: write_file {ARGS}: {{"filename": "a.txt", "content": "a"}}\n'
            f'{FUNC}: read_text {ARGS}: {{not json at all')

    calls = _calls(engine, text)

    assert calls[0]["arguments"] == {"filename": "a.txt", "content": "a"}
    assert "__parse_error__" in calls[1]["arguments"], (
        "the broken block still has to report a parse error, not vanish")


def test_a_single_call_still_parses_the_same_way(engine):
    args = _call(engine, f'{FUNC}: read_text {ARGS}: {{"filename": "a.txt"}}')
    assert args == {"filename": "a.txt"}
