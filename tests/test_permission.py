import os, sys, json
from contextlib import nullcontext
from pathlib import Path

# Data files live inside the package (that is what the engine loads and what ships
# in the wheel) — never at the repo root.
PKG = Path(__file__).resolve().parent.parent / "src" / "agent8088"
os.environ['AGENT8088_CONFIG'] = str(PKG / 'config.txt')

# Add src/ to path so `agent8088` package is importable, then load the engine module
sys.path.insert(0, str(Path('src').resolve()))
from agent8088 import engine as A

# Load the packaged tool specs
A.TOOL_SPECS = A.load_tool_specs(PKG / 'tools.txt', A.APP_CONFIG)
A.TOOL_NAMES = set(A.TOOL_SPECS.keys())

def setup_function():
    A.PERMISSION_MODE = "readonly"
    A._one_shot_grant = False
    A._local_fallback_grant = False
    A._remote_git_grant = False
    A.SANDBOX_BACKEND = "local"

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

def test_safe_action_does_not_consume_one_shot_grant():
    A.grant_escalation()
    assert A.check_permission("read_text") is True
    assert A.check_permission("write_text") is True
    assert A.check_permission("write_text") is False

def test_run_tool_blocks_write_in_readonly(tmp_path, monkeypatch):
    A.PERMISSION_MODE = "readonly"
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    result = A.run_tool(
        "write_file",
        {"filename": str(tmp_path / "test_perm.txt"), "content": "hello"},
    )
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


def test_write_can_replace_file_too_large_to_diff(tmp_path, monkeypatch):
    A.PERMISSION_MODE = "edit"
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    target = tmp_path / "large.txt"
    target.write_bytes(b"x" * (A.MAX_READ_BYTES + 1))

    result = A.run_tool("write_file", {"filename": str(target), "content": "small"})

    assert "Wrote" in result
    assert target.read_text() == "small"


def test_file_path_alias_round_trips(tmp_path, monkeypatch):
    A.PERMISSION_MODE = "edit"
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    target = tmp_path / "alias.txt"

    result = A.run_tool("write_file", {"file_path": str(target), "content": "hello"})

    assert "Wrote" in result
    assert A.run_tool("read_text", {"filepath": str(target)}) == "hello"


def test_run_tool_allows_read_in_readonly(tmp_path, monkeypatch):
    A.PERMISSION_MODE = "readonly"
    # Use a fixture file rather than a real repo file, so the test does not depend
    # on repo layout (and never reads anything outside its own tmp dir).
    target = tmp_path / "sample.txt"
    target.write_text("execute_shell|Execute a shell command\n")
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])

    result = A.run_tool("read_text", {"filename": str(target)})

    assert "execute_shell" in result


def test_read_text_rejects_sensitive_symlink(tmp_path, monkeypatch):
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=secret")
    link = tmp_path / "notes.txt"
    link.symlink_to(secret)
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])

    result = A.run_tool("read_text", {"filename": str(link)})
    assert "sensitive file denied" in result

def test_run_tool_blocks_dangerous_shell_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("execute_shell", {"command": "rm -rf /tmp/nonexistent_perm_test"})
    assert "ESCALATION_REQUEST" in result

def test_run_tool_allows_safe_shell_in_readonly():
    A.PERMISSION_MODE = "readonly"
    result = A.run_tool("execute_shell", {"command": "ls"})
    assert "ESCALATION_REQUEST" not in result

def test_readonly_git_inspection_depends_on_sandbox(monkeypatch):
    monkeypatch.setattr(A, "_resolve_sandbox_backend", lambda: "native")
    for subcommand in ("status", "diff", "log", "show", "branch"):
        assert A.check_permission("shell", f"git {subcommand}") is True
    monkeypatch.setattr(A, "_resolve_sandbox_backend", lambda: "local")
    for subcommand in ("status", "diff", "log", "show"):
        assert A.check_permission("shell", f"git {subcommand}") is False
    assert A.check_permission("shell", "git branch") is True
    monkeypatch.setattr(A, "_resolve_sandbox_backend", lambda: "docker")
    assert A.check_permission("shell", "git show HEAD:.env", host=True) is False
    for subcommand in ("clone", "commit", "push", "reset", "checkout"):
        assert A.check_permission("shell", f"git {subcommand}") is False


def test_readonly_shell_rejects_mutation_bypasses():
    commands = (
        "echo changed > /tmp/changed",
        "python -c \"open('/tmp/changed','w').write('x')\"",
        "pip install example",
        "find . -delete",
        "git branch feature",
        "git diff --output=/tmp/changed",
    )
    assert all(not A.check_permission("shell", command) for command in commands)


def test_hard_git_blocks_survive_edit_and_one_shot_grants():
    A.PERMISSION_MODE = "edit"
    for command in (
        "git push",
        "git reset --hard HEAD",
        "git branch -D main",
        "git status && git push",
        "git -C . push origin HEAD",
        "sh -c 'git push'",
        "bash -lc 'git reset --hard HEAD'",
        "sh -c \"sh -c 'git branch -D main'\"",
    ):
        A.grant_escalation()
        assert A.check_permission("shell", command) is False
        assert "forbidden" in A.run_tool("execute_shell", {"command": command}).lower()


def test_write_path_zones_are_enforced(tmp_path, monkeypatch):
    no_prompt = tmp_path / "scratch"
    prompt = tmp_path / "project"
    blocked = tmp_path / "blocked"
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(A, "NO_PROMPT_PATHS", [no_prompt])
    monkeypatch.setattr(A, "PROMPT_PATHS", [prompt])
    monkeypatch.setattr(A, "BLOCKED_PATHS", [blocked])

    assert "Wrote" in A.run_tool(
        "write_file", {"filename": str(no_prompt / "a.txt"), "content": "ok"})
    assert "ESCALATION_REQUEST" in A.run_tool(
        "write_file", {"filename": str(prompt / "a.txt"), "content": "ok"})
    A.PERMISSION_MODE = "edit"
    assert "blocked" in A.run_tool(
        "write_file", {"filename": str(blocked / "a.txt"), "content": "no"}).lower()


def test_cron_and_browser_require_one_shot_approval(monkeypatch):
    cron_calls = []
    browser_calls = []
    monkeypatch.setattr(A, "_exec_cron", lambda args: cron_calls.append(args) or "scheduled")
    monkeypatch.setattr(A, "_exec_browser", lambda args: browser_calls.append(args) or "loaded")
    monkeypatch.setattr(A, "_ssrf_check", lambda url: None)

    cron_args = {"action": "add", "schedule": "0 9 * * *", "task": "report"}
    assert "ESCALATION_REQUEST" in A.run_tool("schedule_task", cron_args)
    A.grant_escalation()
    assert A.run_tool("schedule_task", cron_args) == "scheduled"

    browser_args = {"url": "https://example.com"}
    assert "ESCALATION_REQUEST" in A.run_tool("browse_page", browser_args)
    A.grant_escalation()
    assert A.run_tool("browse_page", browser_args) == "loaded"
    assert len(cron_calls) == len(browser_calls) == 1


def test_plan_approves_and_retries_exact_structured_step(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(A, "PROMPT_PATHS", [tmp_path])
    target = tmp_path / "plan.txt"
    approvals = []
    result = A._exec_plan(
        {"steps": [{
            "step": "write plan output",
            "tool": "write_file",
            "arguments": {"filename": str(target), "content": "done"},
        }]},
        on_escalation=lambda request: approvals.append(request) or A.grant_escalation() or True,
    )
    assert target.read_text() == "done"
    assert len(approvals) == 1
    assert "Wrote" in result


def test_plan_reports_missing_arguments_instead_of_crashing():
    result = A._exec_plan({"steps": [{"step": "write a file", "tool": "write_file"}]})
    assert "requires arguments" in result
    assert "filename" in result

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
    # Assert against the PACKAGED system.md — that is what actually ships and loads.
    sp = (PKG / 'system.md').read_text(encoding='utf-8')
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
