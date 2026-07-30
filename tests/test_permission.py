import os, sys, json
from contextlib import nullcontext
from pathlib import Path

os.environ['AGENT8088_CONFIG'] = str(Path('config.txt').resolve())

# Add src/ to path so `agent8088` package is importable, then load the engine module
sys.path.insert(0, str(Path('src').resolve()))
from agent8088 import engine as A

# Load tools from local tools.txt
A.TOOL_SPECS = A.load_tool_specs(Path('tools.txt'), A.APP_CONFIG)
A.TOOL_NAMES = set(A.TOOL_SPECS.keys())

def setup_function():
    A.PERMISSION_MODE = "readonly"
    A._one_shot_grant = False

def test_permission_mode_defaults_to_readonly():
    assert A.PERMISSION_MODE == "readonly"

def test_check_permission_blocks_write_in_readonly():
    A.PERMISSION_MODE = "readonly"
    assert A.check_permission("write_text") is False
    assert A.check_permission("shell") is False

def test_check_permission_allows_read_in_readonly():
    A.PERMISSION_MODE = "readonly"
    assert A.check_permission("read_text") is True
    assert A.check_permission("http_get") is False
    assert A.check_permission("last_output") is True

def test_check_permission_allows_all_in_edit():
    A.PERMISSION_MODE = "edit"
    assert A.check_permission("write_text") is True
    assert A.check_permission("shell") is True

def test_escalation_request_returns_structured_message():
    A.PERMISSION_MODE = "readonly"
    result = A.request_escalation(
        target_mode="edit",
        paths=["/tmp/test.txt"],
        change_type="new_file",
        reason="Need to create test.txt"
    )
    assert "ESCALATION_REQUEST" in result
    assert "edit" in result
    assert "test.txt" in result

def test_escalation_grants_one_blocked_action():
    A.grant_escalation()
    assert A.PERMISSION_MODE == "readonly"
    assert A.check_permission("write_text") is True
    assert A.check_permission("write_text") is False

def test_run_tool_blocks_write_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("write_file", {"filename": "/tmp/test_perm.txt", "content": "hello"})
    assert "ESCALATION_REQUEST" in result


def test_run_agent_retries_an_approved_write(engine, tmp_path, monkeypatch):
    from tests.conftest import ScriptedModel

    target = tmp_path / "approved.txt"
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "create_completion", ScriptedModel([
        f'✿FUNCTION✿: write_file ✿ARGS✿: {{"filename": "{target}", "content": "ok"}}',
        f'✿FUNCTION✿: write_file ✿ARGS✿: {{"filename": "{target}", "content": "ok"}}',
        "Done.",
    ]))
    approvals = []

    def approve(name, result):
        approvals.append((name, result))
        engine.grant_escalation()
        return True

    answer = engine.run_agent(
        [{"role": "user", "content": "write the file"}],
        on_escalation=approve,
    )

    assert answer == "Done."
    assert target.read_text() == "ok"
    assert approvals and approvals[0][0] == "write_file"


def test_direct_tool_retries_an_approved_action(monkeypatch):
    from agent8088 import cli

    calls = []
    monkeypatch.setattr(cli, "_active_tool_specs", lambda: {"write_file": {}})
    monkeypatch.setattr(cli, "status_cm", lambda _: nullcontext())
    monkeypatch.setattr(cli.console, "print", lambda *_: None)
    monkeypatch.setattr(cli, "_handle_escalation", lambda _: True)

    def exec_tool(name, arguments):
        calls.append((name, arguments))
        return "ESCALATION_REQUEST:edit:new_file:test.txt:blocked" if len(calls) == 1 else "Wrote 2 bytes"

    monkeypatch.setattr(cli.A, "exec_tool", exec_tool)
    cli.cmd_tool('write_file {"filename": "test.txt", "content": "ok"}')

    assert len(calls) == 2

def test_run_tool_allows_write_in_edit(tmp_path, monkeypatch):
    A.PERMISSION_MODE = "edit"
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    target = tmp_path / "test_perm_edit.txt"
    result = A.run_tool("write_file", {"filename": str(target), "content": "hello"})
    assert "Wrote" in result


def test_file_path_alias_round_trips(tmp_path, monkeypatch):
    A.PERMISSION_MODE = "edit"
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    target = tmp_path / "alias.txt"

    result = A.run_tool("write_file", {"file_path": str(target), "content": "hello"})

    assert "Wrote" in result
    assert A.run_tool("read_text", {"filepath": str(target)}) == "hello"


def test_run_tool_allows_read_in_readonly():
    A.PERMISSION_MODE = "readonly"
    # read_text on tools.txt should work
    result = A.run_tool("read_text", {"filename": "tools.txt"})
    assert "execute_shell" in result

def test_run_tool_blocks_dangerous_shell_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("execute_shell", {"command": "rm -rf /tmp/nonexistent_perm_test"})
    assert "ESCALATION_REQUEST" in result

def test_run_tool_allows_safe_shell_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("execute_shell", {"command": "ls"})
    assert "ESCALATION_REQUEST" not in result

def test_readonly_git_allows_only_inspection():
    for subcommand in ("status", "diff", "log", "show", "branch"):
        assert A.check_permission("shell", f"git {subcommand}") is True
    for subcommand in ("clone", "commit", "push", "reset", "checkout"):
        assert A.check_permission("shell", f"git {subcommand}") is False

def test_escalation_tool_is_not_model_callable():
    assert "request_permission_escalation" not in A.TOOL_NAMES

def test_removed_escalation_tool_is_unknown():
    result = A.exec_tool("request_permission_escalation", json.dumps({
        "target_mode": "edit",
        "paths": "/tmp/test.txt",
        "change_type": "new_file",
        "reason": "Need to write test.txt"
    }))
    assert result == "Unknown tool: request_permission_escalation"

def test_system_prompt_contains_security_instructions():
    from pathlib import Path
    sp = Path('system.md').read_text(encoding='utf-8')
    assert "Never try to fetch internal or private addresses" in sp
    assert "Security & Confidentiality" in sp
    assert "request_permission_escalation" not in sp

def test_escalation_message_format():
    A.PERMISSION_MODE = "readonly"
    msg = A.request_escalation("edit", ["/tmp/test.txt"], "new_file", "Write test.txt")
    # Must start with ESCALATION_REQUEST: and contain the mode, change_type, paths, reason
    parts = msg.split(":", 4)
    assert parts[0] == "ESCALATION_REQUEST"
    assert parts[1] == "edit"
    assert parts[2] == "new_file"
    assert "/tmp/test.txt" in parts[3]
    assert "Write test.txt" in parts[4]

def test_grant_escalation_does_not_persist():
    A.grant_escalation()
    assert A.check_permission("shell", "rm file") is True
    assert A.check_permission("shell", "rm file") is False

def test_env_var_sets_edit_mode():
    import importlib
    from agent8088 import engine as engine_mod
    os.environ['AGENT8088_PERMISSION'] = 'edit'
    # Reload the module to pick up the env var
    A2 = importlib.reload(engine_mod)
    assert A2.PERMISSION_MODE == "edit"
    del os.environ['AGENT8088_PERMISSION']

def test_env_var_defaults_to_readonly():
    import importlib
    from agent8088 import engine as engine_mod
    os.environ.pop('AGENT8088_PERMISSION', None)
    A3 = importlib.reload(engine_mod)
    assert A3.PERMISSION_MODE == "readonly"
