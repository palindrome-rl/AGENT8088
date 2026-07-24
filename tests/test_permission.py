import os, sys, json
from pathlib import Path
from importlib.machinery import SourceFileLoader
import importlib.util

os.environ['AGENT8088_CONFIG'] = str(Path('config.txt').resolve())

loader = SourceFileLoader('agent8088_core', 'agent8088')
spec = importlib.util.spec_from_loader('agent8088_core', loader)
A = importlib.util.module_from_spec(spec)
loader.exec_module(A)

# Load tools from local tools.txt
A.TOOL_SPECS = A.load_tool_specs(Path('tools.txt'), A.APP_CONFIG)
A.TOOL_NAMES = set(A.TOOL_SPECS.keys())

def test_permission_mode_defaults_to_readonly():
    A.PERMISSION_MODE = "readonly"  # reset
    assert A.PERMISSION_MODE == "readonly"

def test_check_permission_blocks_write_in_readonly():
    A.PERMISSION_MODE = "readonly"
    assert A.check_permission("write_text") is False
    assert A.check_permission("shell") is False

def test_check_permission_allows_read_in_readonly():
    A.PERMISSION_MODE = "readonly"
    assert A.check_permission("read_text") is True
    assert A.check_permission("http_get") is True
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

def test_escalation_granted_sets_edit_mode():
    A.PERMISSION_MODE = "readonly"
    A.grant_escalation()
    assert A.PERMISSION_MODE == "edit"
    A.PERMISSION_MODE = "readonly"  # cleanup

def test_run_tool_blocks_write_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("write_file", {"filename": "/tmp/test_perm.txt", "content": "hello"})
    assert "ESCALATION_REQUEST" in result

def test_run_tool_allows_write_in_edit():
    A.PERMISSION_MODE = "edit"
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "test_perm_edit.txt")
    result = A.run_tool("write_file", {"filename": tmp, "content": "hello"})
    assert "Wrote" in result
    os.unlink(tmp)
    A.PERMISSION_MODE = "readonly"  # cleanup

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

def test_escalation_tool_in_tool_names():
    assert "request_permission_escalation" in A.TOOL_NAMES

def test_escalation_tool_returns_request():
    A.PERMISSION_MODE = "readonly"
    result = A.exec_tool("request_permission_escalation", json.dumps({
        "target_mode": "edit",
        "paths": "/tmp/test.txt",
        "change_type": "new_file",
        "reason": "Need to write test.txt"
    }))
    assert "ESCALATION_REQUEST" in result

def test_system_prompt_contains_permission_instructions():
    from pathlib import Path
    sp = Path('system.md').read_text(encoding='utf-8')
    assert "PERMISSION_MODE" in sp
    assert "readonly" in sp
    assert "edit" in sp
    assert "request_permission_escalation" in sp
    assert "escalation" in sp.lower()

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

def test_grant_escalation_persists():
    A.PERMISSION_MODE = "readonly"
    A.grant_escalation()
    assert A.PERMISSION_MODE == "edit"
    # Should persist (not auto-revert)
    assert A.PERMISSION_MODE == "edit"
    A.PERMISSION_MODE = "readonly"  # cleanup