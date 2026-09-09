import json

import pytest


@pytest.mark.parametrize("name", ["read_text", "write_file", "describe_tool"])
def test_describes_exact_live_schema(engine, name):
    report = json.loads(engine.describe_tool(name))
    assert report["name"] == name
    assert report["parameters"] == engine.build_tools_def({name: engine.TOOL_SPECS[name]})[0]["function"]["parameters"]
    assert "not executed" in report["execution"]


@pytest.mark.parametrize("name", [None, "", " ", [], 42, "x" * 201, "nonexistent_tool"])
def test_invalid_names_are_actionable(engine, name):
    result = engine.describe_tool(name)
    assert result.startswith("Error:")
    assert "/tools" in result


def test_registry_scope_and_whitespace(engine):
    assert json.loads(engine.describe_tool(" read_text "))["name"] == "read_text"
    assert engine.describe_tool("read_text", {}).startswith("Error:")


def test_description_does_not_dispatch(engine, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Description must not execute a tool")
    monkeypatch.setattr(engine, "exec_tool", forbidden)
    monkeypatch.setattr(engine, "run_tool", forbidden)
    assert json.loads(engine.describe_tool("write_file"))["name"] == "write_file"


@pytest.mark.parametrize("mode", ["readonly", "full-auto", "plan-only"])
def test_runtime_dispatch(engine, monkeypatch, mode):
    monkeypatch.setattr(engine, "PERMISSION_MODE", mode)
    result = engine.exec_tool("describe_tool", json.dumps({"tool_name": "read_text"}))
    assert json.loads(result)["name"] == "read_text"


def test_cli_aliases_never_execute(engine, monkeypatch):
    from io import StringIO
    from rich.console import Console
    from agent8088 import cli
    output = StringIO()
    monkeypatch.setattr(cli, "A", engine)
    monkeypatch.setattr(cli, "console", Console(file=output, width=200))
    monkeypatch.setattr(cli, "_active_tool_specs", lambda: engine.TOOL_SPECS)
    def forbidden(*args, **kwargs):
        pytest.fail("CLI description invoked a tool")
    monkeypatch.setattr(engine, "exec_tool", forbidden)
    for command, argument in [(cli.cmd_tools, "write_file"), (cli.cmd_tool, "describe write_file")]:
        output.seek(0)
        output.truncate()
        command(argument)
        assert json.loads(output.getvalue())["name"] == "write_file"
    cli.cmd_tool("describe")
    assert "Provide one exact tool name" in output.getvalue()
    output.seek(0)
    output.truncate()
    cli.cmd_tools("")
    assert "read_text" in output.getvalue()
    assert "write_file" in output.getvalue()


@pytest.mark.parametrize("name,status", [("read_text", 200), ("unknown_xyz", 404), ("", 400), ("x" * 201, 400)])
def test_web_lookup(engine, monkeypatch, name, status):
    from fastapi.testclient import TestClient
    from agent8088 import web_server
    monkeypatch.setattr(web_server, "_eng", lambda: engine)
    response = TestClient(web_server.app).get("/api/capabilities", params={"tool_name": name})
    assert response.status_code == status
    if status == 200:
        assert json.loads(response.json()["report"])["name"] == name
    else:
        assert "error" in response.json()


def test_mcp_exposes_description():
    from agent8088 import mcp_server
    assert "describe_tool" in mcp_server.EXPOSED_TOOLS


def test_nested_mcp_schema_and_live_updates(engine):
    schema = {"type": "object", "properties": {"options": {"type": "object", "properties": {
        "format": {"type": "string", "enum": ["json", "text"]}}}}, "required": ["options"]}
    specs = {"mcp_example": {"mode": "mcp", "description": "Example", "parameters": schema}}
    assert json.loads(engine.describe_tool("mcp_example", specs))["parameters"] == schema
    specs["mcp_example"]["description"] = "Updated description"
    assert json.loads(engine.describe_tool("mcp_example", specs))["description"] == "Updated description"


def test_web_full_report_compatibility(engine, monkeypatch):
    from fastapi.testclient import TestClient
    from agent8088 import web_server
    monkeypatch.setattr(web_server, "_eng", lambda: engine)
    monkeypatch.setattr(engine, "describe_capabilities", lambda: "full report")
    assert TestClient(web_server.app).get("/api/capabilities").json() == {"report": "full report"}


def test_cli_completion(engine, monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli, "_active_tool_specs", lambda: {"read_text": engine.TOOL_SPECS["read_text"]})
    assert cli._live_matches("/tools read") == ("read", ["read_text"])
    assert cli._live_matches("/tool describe read") == ("read", ["read_text"])


def test_mcp_handler_uses_same_lookup(engine):
    import asyncio
    from agent8088.mcp_server import _make_handler
    handler = _make_handler("describe_tool", ["tool_name"], engine)
    result = asyncio.run(handler(tool_name="write_file"))
    assert json.loads(result)["name"] == "write_file"


def test_web_tool_invocation(engine, monkeypatch):
    from fastapi.testclient import TestClient
    from agent8088 import web_server
    monkeypatch.setattr(web_server, "_eng", lambda: engine)
    response = TestClient(web_server.app).post("/api/tool/describe_tool", json={"tool_name": "write_file"})
    assert response.status_code == 200
    assert json.loads(response.json()["result"])["name"] == "write_file"


def test_private_execution_fields_are_not_reported(engine, monkeypatch):
    spec = dict(engine.TOOL_SPECS["read_text"], command="private-command", api_key="private-value")
    calls = []
    def redact(text):
        calls.append(text)
        return text
    monkeypatch.setattr(engine, "_redact_secrets", redact)
    result = engine.describe_tool("read_text", {"read_text": spec})
    assert len(calls) == 1
    assert "private-command" not in result
    assert "private-value" not in result
