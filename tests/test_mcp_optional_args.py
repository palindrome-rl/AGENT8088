"""tools.txt's `optional=` must survive into the MCP input schema.

_make_handler synthesizes a signature so FastMCP can generate a real schema.
It gave every argument the same treatment, so an optional one became a required
field and external clients could not call the tool at all: read_text demanded
offset and limit, last_output demanded ref, offset and length. The spec already
records which arguments are optional; the handler just has to read it.
"""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from agent8088.mcp_server import _make_handler, create_mcp_server


def _engine(recorder=None):
    return SimpleNamespace(
        PERMISSION_MODE="readonly",
        run_tool=lambda name, args: (recorder.append((name, args))
                                     if recorder is not None else None) or "ok",
    )


# --- the synthesized signature --------------------------------------------
def test_optional_arguments_get_a_default_so_the_schema_drops_them():
    handler = _make_handler("read_text", ["filename", "offset", "limit"],
                            _engine(), optional=["offset", "limit"])

    params = inspect.signature(handler).parameters

    assert params["filename"].default is inspect.Parameter.empty
    assert params["offset"].default is None
    assert params["limit"].default is None


def test_a_tool_with_no_optional_arguments_is_unchanged():
    handler = _make_handler("calculate", ["expression"], _engine())

    params = inspect.signature(handler).parameters

    assert list(params) == ["expression"]
    assert params["expression"].default is inspect.Parameter.empty


def test_omitted_optional_arguments_are_not_passed_to_the_engine():
    calls = []
    handler = _make_handler("read_text", ["filename", "offset", "limit"],
                            _engine(calls), optional=["offset", "limit"])

    asyncio.run(handler(filename="notes.txt"))

    assert calls == [("read_text", {"filename": "notes.txt"})]


def test_supplied_optional_arguments_still_reach_the_engine():
    calls = []
    handler = _make_handler("read_text", ["filename", "offset", "limit"],
                            _engine(calls), optional=["offset", "limit"])

    asyncio.run(handler(filename="notes.txt", offset="10"))

    assert calls == [("read_text", {"filename": "notes.txt", "offset": "10"})]


# --- the schema an external MCP client actually sees -----------------------
def _schema_for(tool_name, allow_writes=False):
    """Build the real server and read back one tool's published input schema."""
    from agent8088 import engine as A

    previous = A.APP_CONFIG
    if allow_writes:
        A.APP_CONFIG = {**previous, "mcp_server_allow_writes": "1"}
    try:
        server = create_mcp_server()
        tools = asyncio.run(server.list_tools())
    finally:
        A.APP_CONFIG = previous
    for tool in tools:
        if tool.name == tool_name:
            return tool.inputSchema
    return None


@pytest.mark.parametrize("tool_name, required, not_required", [
    ("read_text", {"filename"}, {"offset", "limit"}),
    ("last_output", set(), {"ref", "offset", "length"}),
])
def test_the_published_schema_only_requires_what_the_spec_requires(
        tool_name, required, not_required):
    schema = _schema_for(tool_name)
    if schema is None:
        pytest.skip(f"{tool_name} is not exposed over MCP in this build")

    published = set(schema.get("required", []) or [])

    assert required <= published
    assert not (published & not_required)


def test_write_file_publishes_both_content_sources_as_optional():
    schema = _schema_for("write_file", allow_writes=True)
    if schema is None:
        pytest.skip("write_file is not exposed over MCP in this build")

    published = set(schema.get("required", []) or [])

    assert "filename" in published
    assert not (published & {"content", "source_url"})
