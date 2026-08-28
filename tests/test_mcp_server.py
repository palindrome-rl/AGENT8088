import asyncio
import inspect
from types import SimpleNamespace

from agent8088.mcp_server import _make_handler, exposed_tool_names


def test_mcp_write_surface_requires_explicit_opt_in():
    assert "write_file" not in exposed_tool_names({})
    assert "write_file" in exposed_tool_names({"mcp_server_allow_writes": "1"})


def test_mcp_handler_has_structured_signature_and_restores_mode():
    calls = []
    engine = SimpleNamespace(
        PERMISSION_MODE="readonly",
        run_tool=lambda name, args: calls.append((name, args)) or "42",
    )
    handler = _make_handler("calculate", ["expression"], engine)

    result = asyncio.run(handler(expression="6 * 7"))

    assert result == "42"
    assert calls == [("calculate", {"expression": "6 * 7"})]
    assert engine.PERMISSION_MODE == "readonly"
    assert list(inspect.signature(handler).parameters) == ["expression"]
