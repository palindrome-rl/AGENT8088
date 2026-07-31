import os
import time
from io import BytesIO
from types import SimpleNamespace

import pytest


def test_readonly_local_shell_file_read_requires_approval(engine, tmp_path, monkeypatch):
    fake_secret = tmp_path / ".env"
    fake_secret.write_text("FAKE_TOKEN=not-real")
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "local")
    monkeypatch.setattr(
        engine, "_exec_sandbox_command",
        lambda *_args, **_kwargs: pytest.fail("unapproved command must not execute"),
    )

    result = engine.run_tool("execute_shell", {"command": f"cat {fake_secret}"})

    assert "ESCALATION_REQUEST" in result
    assert "not-real" not in result


def test_resolve_user_path_accepts_path_objects(engine, tmp_path, monkeypatch):
    target = tmp_path / "nested" / "file.txt"
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])

    assert engine.resolve_user_path(target) == target.resolve()


@pytest.mark.parametrize("command", [
    "git status",
    "git diff",
    "git log -p",
    "git show HEAD:.env",
])
def test_readonly_local_git_reads_require_approval(engine, monkeypatch, command):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "local")
    monkeypatch.setattr(
        engine, "_exec_sandbox_command",
        lambda *_args, **_kwargs: pytest.fail("unapproved command must not execute"),
    )

    assert "ESCALATION_REQUEST" in engine.run_tool(
        "execute_shell", {"command": command})


@pytest.mark.parametrize("command", [
    "env git push origin main",
    "command git reset --hard",
    "sudo git branch -D old",
    "time git clean -fd",
    "nohup git restore notes.txt",
    "nice git checkout -- notes.txt",
    "bash -lc 'env git push origin main'",
    "echo $(git push origin main)",
    "echo `git reset --hard`",
    "cat <(git push origin main)",
    "cat >(git reset --hard)",
    "git checkout notes.txt",
    "git checkout --force feature",
    "git stash drop",
    "git stash clear",
])
def test_wrapped_destructive_git_is_blocked(engine, command):
    assert engine._hard_blocked_shell(command), command


def test_windows_quoted_shell_wrappers_are_blocked(engine, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")

    for command in (
        "sh -c 'git push origin main'",
        'bash -c "git reset --hard"',
        'cmd /c "git push"',
        'powershell -Command "git branch -D old"',
        'sh -c "sh -c \'git stash clear\'"',
    ):
        assert engine._hard_blocked_shell(command), command


@pytest.mark.parametrize("command", [
    "echo git push",
    "printf 'git reset --hard'",
    "grep git push fake.txt",
])
def test_git_words_as_data_are_not_blocked(engine, command):
    assert not engine._hard_blocked_shell(command), command


@pytest.mark.parametrize("action", ["add", "remove"])
def test_cron_rejects_newline_injection(engine, monkeypatch, action):
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("invalid cron input must not run crontab"),
    )
    args = {"action": action, "task": "safe\n* * * * * injected"}
    if action == "add":
        args["schedule"] = "0 9 * * *"
    assert "single line" in engine._exec_cron(args)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group behavior")
def test_timeout_kills_descendant_processes(engine, tmp_path):
    marker = tmp_path / "descendant-survived"
    child = (
        "import pathlib,time;"
        "time.sleep(0.8);"
        f"pathlib.Path({str(marker)!r}).write_text('bad')"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        "time.sleep(30)"
    )

    result = engine._exec_process([engine.sys.executable, "-c", parent], timeout=0.2)
    time.sleep(1)

    assert "timed out" in result
    assert not marker.exists()


def test_docker_timeout_forces_named_container_cleanup(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-home")
    seen = {}

    def fake_exec(argv, timeout=25, shell=False):
        seen["argv"] = argv
        return f"Command timed out after {timeout}s."

    def fake_run(argv, **kwargs):
        seen["cleanup"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(engine, "_exec_process", fake_exec)
    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    result = engine._exec_docker_command("print(1)", 1, python_code=True)
    name = seen["argv"][seen["argv"].index("--name") + 1]

    assert "timed out" in result
    assert seen["cleanup"] == ["docker", "rm", "-f", name]


def test_git_clone_terminates_options_and_rejects_remote_helpers(engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda argv, **_kwargs: seen.setdefault("argv", argv) and "done",
    )

    assert engine.run_tool("git_clone", {"url": "--upload-pack=touch", "directory": "dst"}) == "done"
    assert seen["argv"] == ["git", "clone", "--", "--upload-pack=touch", "dst"]
    assert "safe repository URL" in engine.run_tool(
        "git_clone", {"url": "ext::sh -c bad", "directory": "dst"})
    assert engine._structured_tool_argv(
        "git_clone", {"url": "https://example.test/repo.git"}) == [
            "git", "clone", "--", "https://example.test/repo.git",
        ]


def test_shell_uses_posix_fallback_when_bash_is_unavailable(engine, tmp_path, monkeypatch):
    seen = {}

    class FakeProcess:
        stdout = BytesIO(b"")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    monkeypatch.setattr(engine.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        engine.subprocess, "Popen",
        lambda command, **kwargs: seen.update(
            {"command": command, "kwargs": kwargs}) or FakeProcess(),
    )

    assert engine._exec_process("echo fake", shell=True) == "Command completed."
    assert seen["kwargs"]["executable"] == "/bin/sh"


class _RetryableError(Exception):
    status_code = 503


def test_fallback_chain_uses_temporary_provider_and_preserves_model_colon(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "APP_CONFIG", {"fallback_models": "backup:model:cloud"})
    monkeypatch.setattr(engine, "PROVIDERS", {
        "primary": {"model": "main"},
        "backup": {"model": "default"},
    })
    monkeypatch.setattr(engine, "ACTIVE_PROVIDER", "primary")
    monkeypatch.setattr(engine, "DEFAULT_PROVIDER", "primary")
    monkeypatch.setattr(engine, "MODEL_NAME", "main")
    monkeypatch.setattr(engine, "client", "primary-client")
    monkeypatch.setattr(engine, "get_client", lambda provider="": (f"{provider}-client", "default"))

    def fake_completion(client, _messages, _tools, **kwargs):
        calls.append((client, kwargs))
        if client == "primary-client":
            raise _RetryableError("temporary outage")
        return "fallback-response"

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    trace = []
    result = engine._create_completion_with_fallback(
        [], [], temperature=0.1, system_prompt=None, on_token=None,
        interrupt_check=None, trace=trace, turn=2,
    )

    assert result == "fallback-response"
    assert calls[-1][1]["model_name"] == "model:cloud"
    assert (engine.ACTIVE_PROVIDER, engine.MODEL_NAME) == ("primary", "main")
    assert trace[-1]["type"] == "model_fallback"


def test_nonretryable_model_error_does_not_use_fallback(engine, monkeypatch):
    monkeypatch.setattr(engine, "APP_CONFIG", {"fallback_models": "backup:model"})
    monkeypatch.setattr(engine, "PROVIDERS", {"backup": {"model": "model"}})
    monkeypatch.setattr(
        engine, "create_completion",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad request")),
    )
    monkeypatch.setattr(
        engine, "get_client",
        lambda *_args: pytest.fail("non-retryable errors must not fall back"),
    )

    with pytest.raises(ValueError, match="bad request"):
        engine._create_completion_with_fallback(
            [], [], temperature=0.1, system_prompt=None, on_token=None,
            interrupt_check=None, trace=None, turn=0,
        )


def test_stream_interrupt_closes_inflight_response(engine):
    interrupted = False

    def chunk(text):
        delta = SimpleNamespace(content=text, reasoning_content=None, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    class Stream:
        closed = False

        def __iter__(self):
            return iter([chunk("first")])

        def close(self):
            self.closed = True

    stream = Stream()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: stream),
        ),
    )

    def on_token(_kind, _delta):
        nonlocal interrupted
        interrupted = True

    with pytest.raises(engine.AgentInterrupted):
        engine.create_completion(
            client, [], [], on_token=on_token,
            interrupt_check=lambda: interrupted,
        )
    assert stream.closed


def test_stream_interrupt_closes_while_waiting_for_chunk(engine):
    import threading

    interrupted = threading.Event()

    class BlockingStream:
        def __init__(self):
            self.closed = threading.Event()

        def __iter__(self):
            self.closed.wait(2)
            return
            yield

        def close(self):
            self.closed.set()

    stream = BlockingStream()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: stream),
        ),
    )
    timer = threading.Timer(0.1, interrupted.set)
    timer.start()
    try:
        with pytest.raises(engine.AgentInterrupted):
            engine.create_completion(
                client, [], [], on_token=lambda *_args: None,
                interrupt_check=interrupted.is_set,
            )
    finally:
        timer.cancel()

    assert stream.closed.is_set()


@pytest.mark.parametrize(("expression", "expected"), [
    ("2 + 3 * 4", "14"),
    ("7 // 2", "3"),
])
def test_calculator_accepts_bounded_arithmetic(engine, expression, expected):
    assert engine.run_tool("calculate", {"expression": expression}) == expected


@pytest.mark.parametrize("expression", [
    "2 ** 100000000",
    "__import__('os').system('echo bad')",
    "+".join(["1"] * 200),
])
def test_calculator_rejects_unsafe_or_unbounded_work(engine, expression):
    assert engine.run_tool("calculate", {"expression": expression}).startswith("Error:")


def test_tool_argument_parser_preserves_quoted_spaces(engine):
    from agent8088 import cli

    assert cli.parse_tool_args('query="two words" note=\'three words\'') == {
        "query": "two words",
        "note": "three words",
    }


def test_git_push_uses_one_dedicated_remote_confirmation(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda argv, **_kwargs: calls.append(argv) or "pushed",
    )

    assert "git_remote_write" in engine.run_tool("git_push", {})
    engine.grant_escalation()
    assert "git_remote_write" in engine.run_tool("git_push", {})
    engine.grant_escalation("git_remote_write")
    assert engine.run_tool("git_push", {}) == "pushed"
    assert calls == [["git", "push", "origin", "HEAD"]]
    assert "git_remote_write" in engine.run_tool("git_push", {})


def test_short_configured_secrets_are_redacted(engine, monkeypatch):
    monkeypatch.setattr(engine, "APP_CONFIG", {
        "provider.fake.api_key": "tiny",
        "provider.other.api_key": "x",
    })
    monkeypatch.setattr(engine, "_SECRET_VALUES", [])
    assert engine._redact_secrets("token=tiny") == "token=[redacted]"
    assert engine._redact_secrets("value=x") == "value=x"


def test_local_execution_grant_does_not_leak_to_later_action(engine, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    monkeypatch.setattr(engine, "_docker_available", lambda: False)
    monkeypatch.setattr(engine, "_exec_process", lambda *_args, **_kwargs: "ran")
    engine.grant_escalation("local_execution")

    assert engine._exec_sandbox_command("echo fake") == "ran"
    assert engine._one_shot_grant is False
    assert engine._local_fallback_grant is False


def test_missing_http_argument_is_reported_before_ssrf(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "edit")
    monkeypatch.setattr(engine, "SSRF_ALLOW_HOSTS", set())
    result = engine.run_tool("web_search", {})
    assert "unresolved placeholder" in result
    assert "pass query=" in result


def test_model_cache_is_owner_only(tmp_path, monkeypatch):
    from agent8088 import providers

    cache = tmp_path / "models_cache.json"
    cache.write_text("{}")
    cache.chmod(0o644)
    monkeypatch.setattr(providers, "_CACHE_FILE", cache)

    providers._save_disk_cache({"fake": {"ts": 1, "models": ["m"]}})

    assert cache.stat().st_mode & 0o777 == 0o600


def test_windows_private_files_use_current_user_sid(engine, tmp_path, monkeypatch):
    private = tmp_path / "private.json"
    private.write_text("{}")
    calls = []
    monkeypatch.setattr(engine.sys, "platform", "win32")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "whoami":
            return SimpleNamespace(
                returncode=0,
                stdout='"FAKE-PC\\\\tester","S-1-5-21-100-200-300-400"\r\n',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    engine._protect_private_file(private)

    assert calls[1][0] == [
        "icacls", str(private), "/grant:r", "*S-1-5-21-100-200-300-400:(R,W)",
    ]
    assert calls[2][0] == ["icacls", str(private), "/inheritance:r"]
