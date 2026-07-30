from shlex import quote as shlex_quote
from types import SimpleNamespace


def _fake_crontab(calls, current=""):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0 if current else 1, stdout=current, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def test_cron_rejects_bad_schedule(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls))
    out = engine._exec_cron({"action": "add", "schedule": "not a cron", "task": "hi"})
    assert "Invalid" in out
    assert not calls  # crontab never touched


def test_cron_add_builds_entry(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls))
    engine._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "daily report"})
    payload = calls[-1][1]["input"]
    assert "0 9 * * *" in payload
    assert "daily report" in payload


def test_cron_add_requires_task(engine, monkeypatch):
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab([]))
    assert "requires a task" in engine._exec_cron({"action": "add", "schedule": "0 9 * * *"})


def test_cron_list_filters_by_marker(engine, monkeypatch):
    calls = []
    current = "0 9 * * * agent8088 # agent8088\n0 10 * * * backup\n"
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls, current))
    output = engine._exec_cron({"action": "list"})
    assert "# agent8088" in output
    assert "backup" not in output


def test_cron_unknown_action(engine):
    assert "Unknown" in engine._exec_cron({"action": "explode"})


def test_cron_escapes_quotes_in_task(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls))
    task = "it's $(touch /tmp/nope) fine"
    engine._exec_cron({"action": "add", "schedule": "* * * * *", "task": task})
    payload = calls[-1][1]["input"]
    assert shlex_quote(task) in payload


def test_cron_remove_matches_the_shell_quoted_task(engine, monkeypatch):
    calls = []
    task = "it's $(safe) fine"
    current = f"* * * * * agent8088 {shlex_quote(task)} # agent8088\n"
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls, current))
    assert engine._exec_cron({"action": "remove", "task": task}) == "Removed."
    assert calls[-1][1]["input"] == ""


def test_docker_missing_is_graceful(engine, monkeypatch):
    engine.SANDBOX_BACKEND = "auto"
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    monkeypatch.setattr(engine, "_docker_available", lambda: False)
    out = engine._exec_docker({"code": "print(1)"})
    assert "ESCALATION_REQUEST:edit:local_execution:" in out


def test_docker_runs_code_isolated(engine, tmp_path, monkeypatch):
    engine.SANDBOX_BACKEND = "docker"
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    seen = {}

    def fake_process(cmd, timeout=25, shell=False):
        seen["cmd"] = cmd
        seen["shell"] = shell
        return "3"

    monkeypatch.setattr(engine, "_exec_process", fake_process)
    out = engine._exec_docker({"code": "print(1+2)", "image": "python:3.11-slim"})
    assert out == "3"
    cmd = seen["cmd"]
    assert cmd[cmd.index("--network") + 1] == "none"
    assert "--rm" in cmd
    assert "--memory" in cmd
    assert "--cap-drop" in cmd
    assert "--mount" in cmd
    assert seen["shell"] is False


def test_docker_requires_code(engine, monkeypatch):
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    assert "requires 'code'" in engine._exec_docker({})


def test_docker_quotes_code_safely(engine, tmp_path, monkeypatch):
    engine.SANDBOX_BACKEND = "docker"
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    seen = {}
    code = "print('hi'); rm -rf /"
    monkeypatch.setattr(
        engine,
        "_exec_process",
        lambda cmd, **_: seen.setdefault("cmd", cmd) and "",
    )
    engine._exec_docker({"code": code})
    assert seen["cmd"][-3:] == ["python", "-c", code]


def test_docker_masks_workspace_secrets(engine, tmp_path, monkeypatch):
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret")
    engine.SANDBOX_BACKEND = "docker"
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-home")
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    seen = {}
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda command, **_: seen.setdefault("command", command) and "done",
    )
    engine._exec_docker({"code": "print(1)"})
    mounts = [seen["command"][i + 1] for i, value in enumerate(seen["command"][:-1])
              if value == "--mount"]
    assert any("dst=/workspace/.env,readonly" in mount for mount in mounts)


def test_auto_prefers_native_then_docker(engine, monkeypatch):
    engine.SANDBOX_BACKEND = "auto"
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    assert engine._resolve_sandbox_backend() == "native"
    monkeypatch.setattr(
        engine, "_native_sandbox_missing_requirements",
        lambda: ["sandbox-runtime"],
    )
    assert engine._resolve_sandbox_backend() == "docker"


def test_native_sandbox_writes_private_policy(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    path = engine._write_sandbox_settings()
    settings = engine.json.loads(path.read_text())
    assert settings["network"]["allowedDomains"] == []
    assert str(engine.PROJECT_ROOT) in settings["filesystem"]["allowWrite"]
    if engine.sys.platform != "win32":
        assert str(engine.Path("/tmp").resolve()) in settings["filesystem"]["allowWrite"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_approved_local_fallback_runs_once(engine, monkeypatch):
    engine.SANDBOX_BACKEND = "auto"
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    monkeypatch.setattr(engine, "_docker_available", lambda: False)
    monkeypatch.setattr(engine, "_exec_process", lambda *_, **__: "ran locally")
    assert "ESCALATION_REQUEST" in engine._exec_sandbox_command("pwd")
    engine.grant_escalation("local_execution")
    assert engine._exec_sandbox_command("pwd") == "ran locally"
    assert "ESCALATION_REQUEST" in engine._exec_sandbox_command("pwd")


def test_sandbox_backend_setting_persists(engine, tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    monkeypatch.setattr(engine, "CONFIG_PATH", config)
    monkeypatch.setattr(engine, "APP_CONFIG", {})
    status = engine.set_sandbox_backend("local")
    assert status["requested"] == "local"
    assert "sandbox_backend=local" in config.read_text()


def test_browser_missing_is_graceful(engine, monkeypatch):
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)
    out = engine._exec_browser({"url": "https://example.com"})
    assert "Playwright is not installed" in out
    assert "pip install playwright" in out


def test_browser_enforces_ssrf(engine, monkeypatch):
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    out = engine._exec_browser({"url": "http://127.0.0.1/admin"})
    assert "Blocked" in out


def test_browser_requires_url(engine):
    assert "requires 'url'" in engine._exec_browser({})


def test_shell_reports_failure_and_caps_output(engine, monkeypatch):
    monkeypatch.setattr(engine, "MAX_TOOL_OUTPUT_BYTES", 8)
    failed = engine._exec_process(
        [engine.sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"])
    assert "bad" in failed
    assert "status 3" in failed

    large = engine._exec_process(
        [engine.sys.executable, "-c", "print('0123456789abcdef')"])
    assert "truncated at 8 bytes" in large


def test_structured_git_arguments_never_enter_a_shell(engine, monkeypatch):
    title = 'ok"; touch /tmp/injected; echo "'
    seen = {}
    monkeypatch.setattr(
        engine,
        "_exec_process",
        lambda command, **kwargs: seen.update(
            {"command": command, "shell": kwargs.get("shell", False)}
        ) or "created",
    )

    assert engine._exec_structured_tool(
        "git_create_pr", {"title": title, "body": "body"}, 10) == "created"
    assert seen["command"] == [
        "gh", "pr", "create", "--title", title, "--body", "body",
    ]
    assert seen["shell"] is False
