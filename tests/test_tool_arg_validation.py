"""Built-in tool arguments are checked against the tool's declared schema before it runs."""
import json

import pytest


@pytest.fixture
def run(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PLAN_AUDIT = False

    def call(name, **args):
        return engine.exec_tool(name, json.dumps(args))
    call.tmp = tmp_path
    return call


def test_an_invented_argument_name_is_refused_with_a_suggestion(run, engine):
    target = run.tmp / "x.txt"
    result = run("write_file", filename=str(target), text="hello")
    assert "invalid arguments" in result and "'text'" in result and "did you mean 'content'" in result
    assert not target.exists()


def test_a_misspelled_name_gets_the_closest_declared_one(run):
    result = run("write_file", filename="a", contnt="x")
    assert "did you mean 'content'" in result


def test_unknown_with_no_close_match_lists_the_real_arguments(run):
    result = run("write_file", filename="a", content="x", zzz=1)
    assert "'zzz' is not an argument" in result and "did you mean" not in result
    assert "filename, content, source_url, mode" in result


def test_the_refusal_is_a_malformed_call_not_a_failed_step(run, engine):
    result = run("write_file", filename="a", content="x", bogus="y")
    assert engine._is_missing_argument_error("Error: 'write_file' called with invalid arguments: x")
    assert "invalid_arguments" in result  # classified as validation, recoverable
    assert '"recoverable": true' in result.lower().replace(" ", "") or "recoverable" in result


def test_declared_arguments_and_path_aliases_still_work(run):
    target = run.tmp / "ok.txt"
    for key in ("filename", "file", "file_path", "filepath", "path"):
        result = run("write_file", **{key: str(target), "content": "v"})
        assert result.startswith("Wrote "), (key, result)


def test_prose_fields_are_dropped_rather_than_refused(run):
    target = run.tmp / "meta.txt"
    result = run("write_file", filename=str(target), content="v", description="writes it", reason="because")
    assert result.startswith("Wrote ")


def test_internal_underscore_keys_pass(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    result = engine.run_tool("write_file", {"filename": str(tmp_path / "p.txt"), "content": "v", "_private": True})
    assert result.startswith("Wrote ")


def test_execute_shell_keeps_its_timeout_override(run, engine):
    spec = engine.TOOL_SPECS["execute_shell"]
    assert engine._validate_tool_args("execute_shell", spec, {"command": "ls", "timeout": 5}) == ""


def test_subagent_and_plan_aliases_are_tolerated(engine):
    specs = engine.TOOL_SPECS
    assert engine._validate_tool_args("spawn_subagent", specs["spawn_subagent"],
                                      {"type": "coder", "prompt": "do it"}) == ""
    assert engine._validate_tool_args("present_plan", specs["present_plan"], {"text": "1. x"}) == ""


def test_run_sandboxed_keeps_its_code_aliases(engine):
    spec = engine.TOOL_SPECS["run_sandboxed"]
    for key in ("code", "script", "python", "source", "snippet"):
        assert engine._validate_tool_args("run_sandboxed", spec, {key: "print(1)"}) == "", key


def test_wrong_kind_of_value_is_refused(engine):
    spec = engine.TOOL_SPECS["read_text"]
    refused = engine._validate_tool_args("read_text", spec, {"filename": "a", "offset": [1, 2]})
    assert "'offset' must be an integer" in refused
    assert "'limit' must be an integer" in engine._validate_tool_args(
        "read_text", spec, {"filename": "a", "limit": True})
    assert "'arguments' must be a list" in engine._validate_tool_args(
        "cli_anything_run", engine.TOOL_SPECS["cli_anything_run"], {"arguments": 5})


def test_lenient_values_still_pass(engine):
    spec = engine.TOOL_SPECS["read_text"]
    assert engine._validate_tool_args("read_text", spec, {"filename": "a", "offset": "12"}) == ""
    assert engine._validate_tool_args("read_text", spec, {"filename": "a", "offset": "not-a-number"}) == ""
    assert engine._validate_tool_args("read_text", spec, {"filename": "a", "offset": 3.0}) == ""
    assert engine._validate_tool_args("cli_anything_run", engine.TOOL_SPECS["cli_anything_run"],
                                      {"arguments": '["a", "b"]'}) == ""


def test_none_values_are_left_to_the_tool(engine):
    spec = engine.TOOL_SPECS["read_text"]
    assert engine._validate_tool_args("read_text", spec, {"filename": "a", "offset": None}) == ""


def test_tools_without_declared_arguments_are_not_checked(engine):
    spec = engine.TOOL_SPECS["describe_capabilities"]
    assert engine._validate_tool_args("describe_capabilities", spec, {"anything": 1}) == ""


def test_mcp_tools_are_not_checked_here(engine):
    spec = {"mode": "mcp", "args": ["a"], "parameters": {"type": "object", "properties": {}}}
    assert engine._validate_tool_args("some_mcp_tool", spec, {"b": 1}) == ""


def test_a_missing_path_uses_the_shared_missing_argument_message(run, engine):
    result = run("write_file", content="x")
    assert "was called with no arguments" in result and "'filename'" in result
    assert "requires a file path" not in result


def test_the_loop_corrects_an_invented_argument_and_retries(engine, monkeypatch, tmp_path):
    """End to end: a bad call is answered with a correction turn, then the fixed call runs."""
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    target = tmp_path / "loop.txt"
    replies = iter([
        '✿FUNCTION✿: write_file ✿ARGS✿: ' + json.dumps({"filename": str(target), "text": "hi"}),
        '✿FUNCTION✿: write_file ✿ARGS✿: ' + json.dumps({"filename": str(target), "content": "hi"}),
        "done",
    ])

    def completion(*_a, **_k):
        text = next(replies, "done")
        from types import SimpleNamespace as N
        return N(choices=[N(message=N(content=text), finish_reason="stop")])

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    answer = engine.run_agent([{"role": "user", "content": "write hi"}], max_turns=6,
                              system_prompt="", tools_def=[], allowed_tools={"write_file"})
    assert answer.startswith("done")
    assert target.read_text(encoding="utf-8") == "hi"
