"""Git tools must not hang on credential prompts, and repo creation must work.

Two defects motivated these tests:

1. `_exec_process` passed no environment and left stdin inherited, so a
   `git push` to an HTTPS remote without a cached credential opened the user's
   terminal and blocked on "Username for 'https://github.com':" — competing
   with the CLI's own input loop until the 120s tool timeout.

2. `git init` cannot run in the native sandbox at all: the sandbox runtime
   hard-denies writes to `.git/config` and `.git/hooks/**` (both are code
   execution vectors), so repo creation had no working path through
   `execute_shell`. It needs a host tool, like every other git tool.
"""
import subprocess

import pytest


# --- 1. non-interactive git ---------------------------------------------

def test_exec_process_disables_git_terminal_prompt(engine, monkeypatch):
    """The child must never be able to open a terminal prompt of its own."""
    seen = {}

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen.update(kwargs)
            self.stdout = __import__("io").BytesIO(b"")
            self.pid = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    engine._exec_process(["git", "push"], timeout=5)

    env = seen.get("env") or {}
    assert env.get("GIT_TERMINAL_PROMPT") == "0"
    assert env.get("GIT_ASKPASS") == ""
    assert env.get("SSH_ASKPASS") == ""
    assert env.get("GCM_INTERACTIVE") == "never"


def test_exec_process_closes_stdin(engine, monkeypatch):
    """Inherited stdin lets a child steal the terminal the REPL is reading."""
    seen = {}

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen.update(kwargs)
            self.stdout = __import__("io").BytesIO(b"")
            self.pid = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    engine._exec_process(["git", "push"], timeout=5)
    assert seen.get("stdin") == subprocess.DEVNULL


def test_ssh_remotes_are_also_non_interactive(engine, monkeypatch):
    """GIT_TERMINAL_PROMPT only covers HTTPS.

    Over SSH the prompt comes from ssh itself — an unknown host key asks
    "Are you sure you want to continue connecting?" and an encrypted key asks
    for a passphrase, both on /dev/tty. Only BatchMode stops those.
    """
    seen = {}

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen.update(kwargs)
            self.stdout = __import__("io").BytesIO(b"")
            self.pid = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def wait(self, timeout=None):
            return 0

    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    engine._exec_process(["git", "push"], timeout=5)
    assert "BatchMode=yes" in seen["env"]["GIT_SSH_COMMAND"]


def test_user_ssh_command_is_extended_not_replaced(engine, monkeypatch):
    """A user who configured their own ssh invocation keeps it."""
    seen = {}

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen.update(kwargs)
            self.stdout = __import__("io").BytesIO(b"")
            self.pid = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def wait(self, timeout=None):
            return 0

    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /keys/deploy")
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    engine._exec_process(["git", "push"], timeout=5)
    value = seen["env"]["GIT_SSH_COMMAND"]
    assert "ssh -i /keys/deploy" in value
    assert "BatchMode=yes" in value


def test_exec_process_preserves_existing_environment(engine, monkeypatch):
    """Overriding the prompt vars must not wipe PATH and friends."""
    seen = {}

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen.update(kwargs)
            self.stdout = __import__("io").BytesIO(b"")
            self.pid = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def wait(self, timeout=None):
            return 0

    monkeypatch.setenv("AGENT8088_TEST_MARKER", "kept")
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    engine._exec_process(["git", "push"], timeout=5)
    assert seen["env"]["AGENT8088_TEST_MARKER"] == "kept"
    assert seen["env"].get("PATH")


@pytest.mark.parametrize("raw", [
    "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
    "fatal: Authentication failed for 'https://github.com/o/r.git/'",
    "Permission denied (publickey).",
    "Host key verification failed.",
    "fatal: Could not read from remote repository.",
])
def test_auth_failure_is_rewritten_into_guidance(engine, raw):
    """A bare 'prompts disabled' tells the model nothing it can act on."""
    hint = engine._git_auth_hint(raw)
    assert hint != raw
    assert "credential" in hint.lower() or "gh auth login" in hint
    assert raw.strip() in hint  # the original error is never discarded


def test_non_auth_output_passes_through_untouched(engine):
    out = "Everything up-to-date"
    assert engine._git_auth_hint(out) == out


def test_git_push_surfaces_auth_hint(engine, monkeypatch):
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda argv, timeout=25: "fatal: Authentication failed for 'https://x/'",
    )
    engine._remote_git_grant = True
    result = engine._exec_structured_tool("git_push", {}, timeout=10)
    assert "gh auth login" in result or "credential" in result.lower()


# --- 2. repo creation on the host ---------------------------------------

def test_git_init_tool_exists_and_runs_on_host(engine):
    spec = engine.TOOL_SPECS.get("git_init")
    assert spec is not None, "git_init tool is not registered"
    assert spec.get("host"), "git_init must run on the host: the native sandbox " \
                             "hard-denies writes to .git/config and .git/hooks"
    assert spec.get("mode") == "shell"


def test_git_init_builds_a_safe_argv(engine):
    argv = engine._structured_tool_argv("git_init", {"directory": "myrepo"})
    assert argv[:2] == ["git", "init"]
    assert "--" in argv and argv[-1] == "myrepo"


def test_git_init_without_directory_targets_cwd(engine):
    argv = engine._structured_tool_argv("git_init", {})
    assert argv == ["git", "init"]


@pytest.mark.parametrize("bad", ["a\nb", "a\0b", "a\rb"])
def test_git_init_rejects_control_characters(engine, bad):
    with pytest.raises(ValueError):
        engine._structured_tool_argv("git_init", {"directory": bad})


def test_git_init_really_creates_a_repo(engine, tmp_path, monkeypatch):
    """End-to-end on the host path — the sandbox path cannot do this at all."""
    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    engine._exec_structured_tool("git_init", {"directory": "created"}, timeout=30)
    assert (tmp_path / "created" / ".git" / "config").is_file()
