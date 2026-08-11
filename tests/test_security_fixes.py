"""Regression tests for three defects found by scripts/verify_everything.py.

1. Sensitive-file protection covered reads but not writes.
2. Shell-mode tools ran inside the sandbox, so git tools failed (no git binary).
3. _hard_blocked_shell over-blocked 'git' appearing as a plain argument.
"""
import pytest


# --------------------------------------------------------------- 1. WRITES
def test_write_to_sensitive_file_is_blocked(engine, tmp_path, monkeypatch):
    """A write must be refused for the same paths a read is refused for."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    target = home / ".ssh" / "authorized_keys"
    target.write_text("ORIGINAL")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")

    out = engine.run_tool("write_file", {"filename": str(target), "content": "PWNED"})

    assert "sensitive" in out.lower()
    assert target.read_text() == "ORIGINAL", "file must be untouched"


@pytest.mark.parametrize("name", [
    ".gitconfig", "id_rsa", ".env", "server.pem", "API_KEY.txt", "config.txt",
])
def test_write_blocked_for_each_sensitive_name(engine, tmp_path, monkeypatch, name):
    target = tmp_path / name
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    out = engine.run_tool("write_file", {"filename": str(target), "content": "x"})
    assert "sensitive" in out.lower()
    assert not target.exists()


def test_ordinary_write_still_allowed(engine, tmp_path, monkeypatch):
    target = tmp_path / "notes.txt"
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    out = engine.run_tool("write_file", {"filename": str(target), "content": "hello"})
    assert "sensitive" not in out.lower()
    assert target.read_text() == "hello"


def test_sensitive_write_override_respected(engine, tmp_path, monkeypatch):
    """allowed_sensitive_files must still un-block an explicitly permitted file."""
    target = tmp_path / ".env"
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(engine, "ALLOWED_SENSITIVE_FILES", [str(target)])
    out = engine.run_tool("write_file", {"filename": str(target), "content": "K=v"})
    assert "denied" not in out.lower(), out
    assert target.read_text() == "K=v"


def test_sensitive_write_override_does_not_match_a_subtree(engine, tmp_path, monkeypatch):
    target = tmp_path / "tests" / ".ssh" / "id_rsa"
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(engine, "ALLOWED_SENSITIVE_FILES", ["test"])

    out = engine.run_tool("write_file", {"filename": str(target), "content": "PWNED"})

    assert "sensitive" in out.lower()
    assert not target.exists()


@pytest.mark.parametrize("command", [
    "cat ~/.agent8088/.env",
    "cat cert.pem",
    "printf x >> ~/.bashrc",
    "true; git show HEAD:.env",
    "git -c core.pager=cat show HEAD:.env",
    r"type %USERPROFILE%\\.aws\\credentials",
])
def test_shell_protected_paths_are_hard_blocked(engine, command):
    assert engine._hard_blocked_shell(command) is True


def test_normal_writes_are_utf8_and_preserve_newlines(engine, tmp_path, monkeypatch):
    target = tmp_path / "notes.txt"
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")

    assert "Wrote" in engine.run_tool("write_file", {
        "filename": str(target), "content": "emoji: 😀\nnext\n"})
    assert target.read_bytes() == "emoji: 😀\nnext\n".encode()


# ----------------------------------------------------------- 2. HOST TOOLS
def test_git_tools_declared_host(engine):
    """Read-only git tools stay sandboxed with a Git-capable image."""
    for name in ("git_clone", "git_commit", "git_push", "git_create_pr"):
        assert engine.TOOL_SPECS[name].get("host"), f"{name} should be host=1"
    for name in ("git_status", "git_diff", "git_log"):
        assert not engine.TOOL_SPECS[name].get("host"), f"{name} should be sandboxed"
        assert engine.TOOL_SPECS[name].get("sandbox_image") == "alpine/git:v2.47.2"


def test_execute_shell_stays_sandboxed(engine):
    """Arbitrary model-supplied commands must NOT get the host escape hatch."""
    assert not engine.TOOL_SPECS["execute_shell"].get("host")


def test_host_shell_tool_bypasses_sandbox(engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(engine, "_exec_process",
                        lambda cmd, timeout=25, shell=False: seen.setdefault("host", cmd) or "ok")
    monkeypatch.setattr(engine, "_exec_sandbox_argv",
                        lambda *a, **k: seen.setdefault("sandbox", True) or "sandboxed")
    engine.run_tool("git_clone", {"url": "https://example.com/repo.git", "directory": "repo"})
    assert "host" in seen, "git_clone must run on the host"
    assert "sandbox" not in seen, "git_clone must not go through the sandbox"


def test_structured_host_tool_bypasses_sandbox(engine, monkeypatch):
    seen = {"argv": [], "sandbox": False}
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")

    def fake_process(cmd, timeout=25, shell=False):
        seen["argv"].append(cmd)
        return "ok"

    monkeypatch.setattr(engine, "_exec_process", fake_process)
    monkeypatch.setattr(engine, "_exec_sandbox_argv",
                        lambda *a, **k: seen.update(sandbox=True) or "sandboxed")
    engine.run_tool("git_commit", {"message": "hello"})
    assert seen["sandbox"] is False, "git_commit must not go through the sandbox"
    # git_commit stages first, then commits — both on the host.
    assert seen["argv"][0] == ["git", "add", "-A"]
    assert seen["argv"][-1] == ["git", "commit", "-m", "hello"]


def test_sandboxed_tool_still_uses_sandbox(engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(engine, "_exec_sandbox_command",
                        lambda *a, **k: seen.setdefault("sandbox", True) or "sandboxed")
    engine.run_tool("execute_shell", {"command": "echo hi"})
    assert seen.get("sandbox"), "execute_shell must remain sandboxed"


def test_host_tool_still_permission_gated(engine, monkeypatch):
    """host=1 must not bypass the permission system."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "_one_shot_grant", False)
    out = engine.run_tool("git_commit", {"message": "x"})
    assert "ESCALATION_REQUEST" in out


def test_host_git_push_requires_dedicated_confirmation(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    out = engine.run_tool("git_push", {})
    assert "ESCALATION_REQUEST\x1fedit\x1fgit_remote_write\x1f" in out


def test_missing_binary_reports_actionably(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(engine, "_exec_sandbox_command",
                        lambda *a, **k: "sh: 1: git: not found\nCommand exited with status 127.")
    out = engine.run_tool("git_status", {})
    assert "not available" in out.lower() or "not found" in out.lower()


# ------------------------------------------------------ 3. SHELL CLASSIFIER
@pytest.mark.parametrize("command", [
    "echo git push",
    "echo 'git push'",
    "grep git push file.txt",
    "printf git reset --hard",
])
def test_git_as_plain_argument_is_not_blocked(engine, command):
    assert engine._hard_blocked_shell(command) is False, command


@pytest.mark.parametrize("command", [
    "git push",
    "git push origin main",
    "git reset --hard",
    "git branch -d x",
    "echo hi; git push",
    "ls && git push origin HEAD",
    "sh -c 'git push'",
    "git -C /tmp push",
    "/usr/bin/git push",
])
def test_real_git_mutations_still_blocked(engine, command):
    assert engine._hard_blocked_shell(command) is True, command
