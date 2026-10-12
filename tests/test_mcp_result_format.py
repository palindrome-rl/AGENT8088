"""MCP tool results reach the model as plain text, not as a serialised envelope."""
import json

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
