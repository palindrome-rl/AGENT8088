"""MCP text results are compact without discarding structured application data."""
import json
import sys

import pytest
from mcp import types

from agent8088 import mcp


def _text(*texts, **kwargs):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text) for text in texts], **kwargs)


def test_a_single_text_block_is_passed_as_plain_text():
    assert mcp.format_result(_text("It is 14:05 in Karachi.")) == "It is 14:05 in Karachi."


def test_quotes_and_unicode_are_not_escaped():
    assert mcp.format_result(_text('{"city": "Zürich"}')) == '{"city": "Zürich"}'


def test_several_text_blocks_are_joined_by_newlines():
    assert mcp.format_result(_text("first", "second")) == "first\nsecond"


def test_a_server_reported_error_is_prefixed():
    assert mcp.format_result(_text("no such file", isError=True)) == "MCP tool error: no such file"


def test_a_result_with_non_text_content_keeps_its_structure_without_empty_fields():
    result = types.CallToolResult(content=[
        types.TextContent(type="text", text="chart ready"),
        types.ImageContent(type="image", data="aGVsbG8=", mimeType="image/png"),
    ])

    rendered = json.loads(mcp.format_result(result))

    assert [block["type"] for block in rendered["content"]] == ["text", "image"]
    assert "_meta" not in rendered
    assert None not in rendered.values()


def test_structured_content_is_kept_when_there_is_no_text():
    result = types.CallToolResult(content=[], structuredContent={"temp_c": 21})

    assert json.loads(mcp.format_result(result))["structuredContent"] == {"temp_c": 21}


@pytest.mark.parametrize("is_error", [False, True])
def test_text_summary_keeps_structured_results(is_error):
    payload = {"records": [{"id": 123}, {"id": 456}]}
    result = _text("Found two records.", structuredContent=payload, isError=is_error)
    rendered = mcp.format_result(result)
    if is_error:
        assert rendered.startswith("MCP tool error: ")
        rendered = rendered.removeprefix("MCP tool error: ")
    data = json.loads(rendered)
    assert data["structuredContent"] == payload
    assert data["content"][0]["text"] == "Found two records."


def test_structured_payload_keeps_null_and_meta_fields():
    payload = {"records": [{"deleted_at": None, "_meta": {"label": "keep"}}],
               "nested": [None, {"value": None}], "_meta": {"business": True}}
    result = _text(structuredContent=payload, _meta={"protocol": "omit"})
    data = json.loads(mcp.format_result(result))
    assert data["structuredContent"] == payload
    assert "_meta" not in data
    assert result.structuredContent == payload
    assert result.meta == {"protocol": "omit"}


def test_empty_structured_payload_is_not_dropped_for_text():
    data = json.loads(mcp.format_result(_text("No records.", structuredContent={})))
    assert data["structuredContent"] == {}


def test_a_plain_dict_is_serialised_as_before():
    assert mcp.format_result({"ok": "t"}) == '{"ok": "t"}'


@pytest.fixture
def runtime(tmp_path):
    rt = mcp.MCPRuntime(tmp_path)
    yield rt
    rt.close()


def test_the_runtime_returns_the_formatted_result(runtime, monkeypatch):
    runtime._tools["mcp_s_t"] = {"mcp_server": "s", "mcp_tool": "t", "timeout": 5}
    monkeypatch.setattr(mcp.MCPRuntime, "_host_finished", lambda self, server: False)

    def run(self, coroutine, timeout=35):
        coroutine.close()
        return _text("hello")

    monkeypatch.setattr(mcp.MCPRuntime, "_run", run)

    assert runtime.call("mcp_s_t", {}) == "hello"


def test_real_stdio_server_preserves_text_and_structured_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    server = tmp_path / "server.py"
    server.write_text('''
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
app = FastMCP("result-contract")
@app.tool()
def records(fail: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="Records returned.")],
        structuredContent={"records": [{"id": 1, "deleted_at": None,
                                        "_meta": {"label": "keep"}}]},
        isError=fail)
@app.tool()
def greeting() -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text="Hello!")])
app.run(transport="stdio")
''', encoding="utf-8")
    rt = mcp.MCPRuntime(tmp_path)
    try:
        rt.set_server("fixture", {"command": sys.executable, "args": [str(server)]},
                      project=True)
        tools = rt.reload()
        assert rt.statuses["fixture"]["state"] == "connected", rt.statuses
        assert "mcp_fixture_records" in tools
        assert rt.call("mcp_fixture_greeting", {}) == "Hello!"
        for fail in (False, True):
            rendered = rt.call("mcp_fixture_records", {"fail": fail})
            assert rendered.startswith("MCP tool error: ") == fail
            data = json.loads(rendered.removeprefix("MCP tool error: "))
            assert data["content"][0]["text"] == "Records returned."
            assert data["structuredContent"]["records"] == [
                {"id": 1, "deleted_at": None, "_meta": {"label": "keep"}}]
    finally:
        rt.close()
