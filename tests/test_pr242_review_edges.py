"""Rejected write calls must not display an earlier edit's diff."""
import pytest

from agent8088 import engine, web_server


@pytest.mark.parametrize("arguments", ["{bad json", "[]", "null"])
def test_malformed_write_does_not_reuse_previous_diff(monkeypatch, arguments):
    monkeypatch.setattr(engine, "_last_write_diff",
                        engine._make_diff("old\n", "new\n", "previous-edit.txt"))
    result = engine.exec_tool("write_file", arguments)
    payload = web_server._tool_result_payload("write_file", result)
    assert "diff" not in payload, payload.get("diff")
