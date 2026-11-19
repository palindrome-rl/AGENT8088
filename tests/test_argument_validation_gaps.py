"""Audit issue 47: textual arguments and MCP schemas are checked, not just 6 tools."""
import pytest


def test_list_passed_for_a_filename_is_refused(engine):
    spec = engine.TOOL_SPECS["read_text"]
    msg = engine._validate_tool_args("read_text", spec, {"filename": ["a.py", "b.py"]})
    assert "'filename' must be text" in msg
    assert engine._validate_tool_args("read_text", spec, {"filename": "a.py"}) == ""


def test_mcp_call_is_checked_against_its_published_schema(engine):
    schema = {"type": "object", "required": ["repo"], "properties": {
        "repo": {"type": "string"}, "limit": {"type": "integer"}, "tags": {"type": "array"}}}
    spec = {"mode": "mcp", "parameters": schema, "args": []}
    check = lambda a: engine._validate_tool_args("mcp_x", spec, a)
    assert check({"repo": "a/b", "limit": 5, "tags": ["x"]}) == ""
    assert "'repo' is required" in check({"limit": 5})
    assert "'limit'" in check({"repo": "a/b", "limit": True})
    assert "'repo' must be text" in check({"repo": {"a": 1}})
    assert "'tags'" in check({"repo": "a/b", "tags": 3})
    assert check({"repo": "a/b", "limit": "5"}) == ""   # numbers sent as text are tolerated


# --- audit issue 41: opt-in native-only prompt ---------------------------------------------------

def test_tool_call_format_native_drops_the_marker_lessons(engine):
    specs = {"read_text": engine.TOOL_SPECS["read_text"]}
    marker = "✿" + "FUNCTION" + "✿"
    default = engine.render_tool_docs(specs, catalogue=False)
    native = engine.render_tool_docs(specs, catalogue=False, markers=False)
    assert marker in default and marker not in native
    assert "tool-calling interface" in native and "Mandatory routing" in native
