"""The unknown-tool correction names the tool-call format the model is actually using."""
from types import SimpleNamespace as NS


def test_instruction_matches_the_active_format(engine):
    native = engine._unknown_tool_call_instruction(True)
    text = engine._unknown_tool_call_instruction(False)

    assert "✿FUNCTION✿" not in native
    assert "tool-calling interface" in native
    assert "✿FUNCTION✿: name ✿ARGS✿" in text


def _run_with_unknown_tool(engine, monkeypatch, native: bool) -> str:
    """Drive one turn where the model calls a tool that does not exist, and
    return the correction the harness sent back."""
    specs = {"read_text": engine.TOOL_SPECS["read_text"]}
    seen = []
    replies = iter([
        NS(choices=[NS(message=NS(content='✿FUNCTION✿: current_time ✿ARGS✿: {}'), finish_reason="stop")]),
        NS(choices=[NS(message=NS(content="done"), finish_reason="stop")]),
    ])

    def completion(messages, _tools, **_kwargs):
        seen.append(list(messages))
        return next(replies)

    monkeypatch.setattr(engine, "TOOL_SELECTION", "full")
    monkeypatch.setattr(engine, "_native_tools_enabled", lambda *_a, **_k: native)
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    engine.run_agent(
        [{"role": "user", "content": "what time is it?"}],
        max_turns=3, system_prompt="", tools_def=lambda: engine.build_tools_def(specs),
        allowed_tools=set(specs), memory_capture=False)
    assert len(seen) == 2, "the correction should cause exactly one more completion"
    return str(seen[1][-1].get("content", ""))


def test_native_backend_is_not_taught_the_marker_syntax(engine, monkeypatch):
    correction = _run_with_unknown_tool(engine, monkeypatch, native=True)

    assert "current_time" in correction
    assert "does not exist" in correction
    assert "tool-calling interface" in correction
    assert "✿FUNCTION✿" not in correction


def test_text_backend_still_gets_the_marker_syntax(engine, monkeypatch):
    correction = _run_with_unknown_tool(engine, monkeypatch, native=False)

    assert "does not exist" in correction
    assert "✿FUNCTION✿: name ✿ARGS✿" in correction
