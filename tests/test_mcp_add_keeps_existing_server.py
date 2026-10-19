"""add_mcp_server must not silently replace a working server (issue sheet row 48)."""
import pytest

WORKING = {"command": "npx", "args": ["-y", "real-mail-mcp"]}


class _Runtime:
    def __init__(self, connects=True):
        self.configs = {"email": dict(WORKING)}
        self.statuses = {"email": {"state": "connected",
                                   "tools": ["mcp_email_list_emails", "mcp_email_read_email"]}}
        self.connects = connects

    def server_config(self, name):
        return self.configs.get(name)

    def set_server(self, name, config, project=False):
        self.configs[name] = config
        ok = config == WORKING or self.connects
        self.statuses[name] = ({"state": "connected", "tools": ["mcp_email_list_emails"]} if ok
                               else {"state": "error", "error": "package not found", "tools": []})


@pytest.fixture
def add(engine, monkeypatch):
    def run(runtime, **extra):
        monkeypatch.setattr(engine, "MCP_RUNTIME", runtime)
        monkeypatch.setattr(engine, "reload_mcp_tools", lambda: runtime.statuses)
        monkeypatch.setattr(engine, "check_permission", lambda *a, **k: True)
        args = {"name": "email", "transport": "stdio", "command": "npx",
                "args": '["-y","made-up-mail-mcp"]', **extra}
        return engine._run_mcp_manage_tool("add_mcp_server", args, approval_key="t")
    return run


def test_same_name_is_refused_and_lists_the_existing_tools(add):
    runtime = _Runtime()
    result = add(runtime)
    assert result.startswith("Error:") and "Nothing was changed" in result
    assert "mcp_email_read_email" in result
    assert runtime.configs["email"] == WORKING


def test_a_failed_replacement_restores_the_working_server(add):
    runtime = _Runtime(connects=False)
    result = add(runtime, replace="true")
    assert "previous configuration was restored" in result
    assert runtime.configs["email"] == WORKING


def test_a_working_replacement_is_kept(add):
    runtime = _Runtime(connects=True)
    result = add(runtime, replace="true")
    assert result.startswith("Added 'email'")
    assert runtime.configs["email"]["args"] == ["-y", "made-up-mail-mcp"]


def test_a_new_name_is_added_as_before(add):
    runtime = _Runtime()
    result = add(runtime, name="calendar")
    assert result.startswith("Added 'calendar'")
    assert runtime.configs["email"] == WORKING
