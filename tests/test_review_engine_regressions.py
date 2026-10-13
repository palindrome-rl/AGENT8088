import json


def test_reasoning_cleanup_preserves_tags_inside_valid_python_string(engine, tmp_path):
    source = 'example = """\n<think>literal template</think>\n</think>\n"""\n'
    target = tmp_path / "example.py"
    assert engine._clean_generated_code(source, target) == (source, False)


def test_fence_cleanup_preserves_tag_lines_inside_valid_python_string(engine, tmp_path):
    source = 'example = """\n<think>template</think>\n</think>\n"""\n'
    target = tmp_path / "example.py"
    raw = "```python\n" + source + "```"
    assert engine._clean_generated_code(raw, target) == (source, True)


def test_reasoning_cleanup_removes_only_an_enclosing_wrapper(engine, tmp_path):
    code = 'def test_ok():\n    assert True\n'
    target = tmp_path / "test_ok.py"
    cleaned, changed = engine._clean_generated_code(
        "<think>\nprivate reasoning\n</think>\n" + code, target)
    assert changed and cleaned == code


def test_reasoning_cleanup_preserves_jsx_and_template_tag_text(engine, tmp_path):
    source = 'const view = `\n<think>template text</think>\n`;\nconst node = <think>literal JSX</think>;\n'
    target = tmp_path / "view.jsx"
    assert engine._clean_generated_code(source, target) == (source, False)


def test_json_calls_mix_in_order_and_nested_examples_are_not_calls(engine):
    text = ('✿FUNCTION✿: read_text ✿ARGS✿: '
            '{"filename": "a.py", "example": {"name": "read_text", '
            '"arguments": {"filename": "fake.py"}}}\n'
            '{"name": "read_text", "arguments": {"filename": "b.py"}}')
    calls = engine.find_tool_calls(text)
    assert [call["arguments"]["filename"] for call in calls] == ["a.py", "b.py"]


def test_native_json_marker_is_not_returned_twice(engine):
    text = '✿{"name": "read_text", "arguments": {"filename": "a.py"}}✿'
    calls = engine.find_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["arguments"]["filename"] == "a.py"


def test_nested_bare_json_example_is_not_a_second_call(engine):
    text = ('{"name": "read_text", "arguments": {"filename": "a.py", '
            '"example": {"name": "execute_shell", "arguments": '
            '{"command": "echo nested"}}}}')
    calls = engine.find_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "read_text"


def test_new_invalid_python_test_is_refused_even_without_cleanup(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    target = repo / "tests" / "test_new.py"
    result = engine.run_tool("write_file", {
        "filename": str(target), "content": "def test_broken(:\n    pass\n"})
    assert "NOT written" in result and "not valid Python" in result
    assert not target.exists()


def test_rewrite_limit_uses_resolved_target_aliases(engine, monkeypatch, tmp_path):
    writes = []
    responses = iter(("repo/../repo/Script.py", "repo/Script.py", None))
    canonical = tmp_path / "repo" / "Script.py"
    monkeypatch.setattr(engine, "resolve_write_path", lambda raw: canonical)

    def model(messages, tools, **kwargs):
        path = next(responses)
        if path is None:
            return type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "done", "tool_calls": []})(),
                "finish_reason": "stop"})()]})()
        call = type("T", (), {"function": type("F", (), {
            "name": "write_file", "arguments": json.dumps({
                "filename": path, "content": "print('ok')\n"})})()})()
        message = type("M", (), {"content": "", "tool_calls": [call]})()
        return type("R", (), {"choices": [type("C", (), {
            "message": message, "finish_reason": "tool_calls"})()]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **kw: writes.append(args) or "Wrote")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "MAX_FILE_REWRITES", 1)
    trace = []
    engine.run_agent([{"role": "user", "content": "write Script.py"}], max_turns=5,
                     trace=trace, dynamic_turns=False)
    assert len(writes) == 1
    assert any(event["type"] == "rewrite_limit" for event in trace)



