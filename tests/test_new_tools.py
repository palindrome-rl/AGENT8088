from shlex import quote as shlex_quote
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest


def _fake_crontab(calls, current=""):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0 if current else 1, stdout=current, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


@pytest.fixture
def posix_cron(engine, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "linux")
    return engine


def test_cron_rejects_bad_schedule(posix_cron, monkeypatch):
    engine = posix_cron
    calls = []
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls))
    out = engine._exec_cron({"action": "add", "schedule": "not a cron", "task": "hi"})
    assert "Invalid" in out
    assert not calls  # crontab never touched


def test_cron_add_builds_entry(posix_cron, monkeypatch):
    engine = posix_cron
    calls = []
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls))
    engine._exec_cron({"action": "add", "schedule": "0 9 * * *", "task": "daily report"})
    payload = calls[-1][1]["input"]
    assert "0 9 * * *" in payload
    assert "daily report" in payload
    assert all(kwargs["timeout"] == 20 for _, kwargs in calls)


def test_cron_add_requires_task(posix_cron, monkeypatch):
    engine = posix_cron
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab([]))
    assert "requires a task" in engine._exec_cron({"action": "add", "schedule": "0 9 * * *"})


def test_cron_list_filters_by_marker(posix_cron, monkeypatch):
    engine = posix_cron
    calls = []
    current = "0 9 * * * agent8088 # agent8088\n0 10 * * * backup\n"
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls, current))
    output = engine._exec_cron({"action": "list"})
    assert "# agent8088" in output
    assert "backup" not in output


def test_cron_unknown_action(posix_cron):
    engine = posix_cron
    assert "Unknown" in engine._exec_cron({"action": "explode"})


def test_cron_escapes_quotes_in_task(posix_cron, monkeypatch):
    engine = posix_cron
    calls = []
    monkeypatch.setattr(engine.subprocess, "run", _fake_crontab(calls))
    task = "it's $(touch /tmp/nope) fine"
    engine._exec_cron({"action": "add", "schedule": "* * * * *", "task": task})
    payload = calls[-1][1]["input"]
    assert shlex_quote(task) in payload


def test_cron_remove_matches_the_shell_quoted_task(posix_cron, monkeypatch):
    engine = posix_cron
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
    assert "sandbox is required" in out.lower()
    assert "ESCALATION_REQUEST" not in out


def test_sandboxed_code_timeout_is_clamped(engine, monkeypatch):
    seen = {}
    engine.MAX_TOOL_TIMEOUT_SECONDS = 90
    monkeypatch.setattr(
        engine, "_exec_sandbox_command",
        lambda _code, **kwargs: seen.update(kwargs) or "ok",
    )

    assert engine._exec_docker({"code": "print(1)", "timeout": 99_999}) == "ok"
    assert seen["timeout"] == 90


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


def test_docker_rejects_option_like_image(engine, tmp_path, monkeypatch):
    engine.SANDBOX_BACKEND = "docker"
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    assert "invalid container image" in engine._exec_docker({
        "code": "print(1)", "image": "--privileged",
    })


def test_docker_quotes_code_safely(engine, docker_image_present, tmp_path, monkeypatch):
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


def test_docker_masks_workspace_secrets(engine, docker_image_present, tmp_path, monkeypatch):
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret")
    skipped_secret = tmp_path / "node_modules" / ".env"
    skipped_secret.parent.mkdir()
    skipped_secret.write_text("DEPENDENCY_TOKEN=secret")
    engine.SANDBOX_BACKEND = "docker"
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
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
    assert not any("node_modules/.env" in mount for mount in mounts)


def test_docker_refuses_unbounded_sensitive_mounts(engine, docker_image_present, tmp_path, monkeypatch):
    for index in range(129):
        (tmp_path / f".env-{index}").write_text("secret")
    engine.SANDBOX_BACKEND = "docker"
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-home")
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda *_args, **_kwargs: pytest.fail("Docker must not run"),
    )

    assert "too many sensitive" in engine._exec_docker({"code": "print(1)"})


def test_windows_docker_mounts_use_container_path_separators(engine, docker_image_present, tmp_path, monkeypatch):
    project = PureWindowsPath("C:/workspace")
    engine.SANDBOX_BACKEND = "docker"
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", project)
    monkeypatch.setattr(engine, "Path", PureWindowsPath)
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-home")
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(
        engine.os, "walk",
        lambda _root: [(str(project), [], [".env"])],
    )
    seen = {}
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda command, **_: seen.setdefault("command", command) and "done",
    )

    assert engine._exec_docker_command(
        "print(1)", 60, python_code=True, workspace=project
    ) == "done"
    mounts = [
        seen["command"][index + 1]
        for index, value in enumerate(seen["command"][:-1])
        if value == "--mount"
    ]
    assert any("dst=/workspace/.env,readonly" in mount for mount in mounts)
    assert not any(r"dst=\workspace" in mount for mount in mounts)


def test_windows_cron_uses_schtasks_and_private_registry(engine, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-home")
    monkeypatch.setattr(engine, "_protect_private_file", lambda _path: None)
    monkeypatch.setattr(engine.shutil, "which", lambda name: name)

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    task = "it's $(Remove-Item *)"

    assert engine._exec_cron({
        "action": "add", "schedule": "0 9 * * *", "task": task,
    }) == "Scheduled: 0 9 * * *"
    create = calls[-1][0]
    assert create[1:3] == ["/Create", "/TN"]
    assert create[create.index("/SC") + 1] == "DAILY"
    assert create[create.index("/ST") + 1] == "09:00"
    assert create[create.index("/RL") + 1] == "LIMITED"
    assert "/IT" in create
    script = next((tmp_path / "agent-home" / "scheduled-tasks").glob("*.ps1"))
    assert task not in script.read_text(encoding="utf-8")
    assert task in engine._exec_cron({"action": "list"})

    assert engine._exec_cron({"action": "remove", "task": task}) == "Removed."
    assert calls[-1][0][1] == "/Delete"
    assert engine._exec_cron({"action": "list"}) == "No scheduled tasks."
    assert not script.exists()


def test_windows_cron_reports_private_script_write_failure(engine, monkeypatch):
    monkeypatch.setattr(engine, "_load_windows_schedules", lambda: [])

    def fail_script_write(*_args):
        raise OSError("access denied")

    monkeypatch.setattr(engine, "_windows_task_script", fail_script_write)

    result = engine._exec_windows_cron(
        "add", "0 9 * * *", "daily report", ["0", "9", "*", "*", "*"],
    )

    assert result == "Windows scheduler error: access denied"


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        ("*/15 * * * *", ["/SC", "MINUTE", "/MO", "15", "/ST", "00:00"]),
        ("30 * * * *", ["/SC", "HOURLY", "/MO", "1", "/ST", "00:30"]),
        ("0 9 * * 1,5", ["/SC", "WEEKLY", "/D", "MON,FRI", "/ST", "09:00"]),
        ("0 9 15 * *", ["/SC", "MONTHLY", "/D", "15", "/ST", "09:00"]),
    ],
)
def test_windows_cron_translates_common_schedules(engine, schedule, expected):
    assert engine._windows_schedule_args(schedule.split()) == expected


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


@pytest.mark.parametrize(
    ("configured", "expected"),
    [({}, "0.0.73"),
     ({"sandbox_runtime_version": "0.0.67"}, "0.0.73"),
     ({"sandbox_runtime_version": "0.0.73"}, "0.0.73"),
     ({"sandbox_runtime_version": "custom-build"}, "custom-build")],
)
def test_sandbox_runtime_upgrades_the_shipped_legacy_pin(engine, configured, expected):
    assert engine._sandbox_runtime_version(configured) == expected


def test_native_sandbox_writes_private_policy(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    path = engine._write_sandbox_settings()
    settings = engine.json.loads(path.read_text())
    assert settings["network"]["allowedDomains"] == []
    assert settings["network"]["strictAllowlist"] is True
    assert str(engine.ARTIFACTS_ROOT) in settings["filesystem"]["allowWrite"]
    assert str(engine.PROJECT_ROOT) not in settings["filesystem"]["allowWrite"]
    assert str((tmp_path / "sandbox-tmp").resolve()) in settings["filesystem"]["allowWrite"]
    if engine.sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600


def test_windows_sandbox_setup_handles_missing_runtime(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine.shutil, "which", lambda name: f"C:\\{name}.exe")
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(stdout="v20.11.0"),
    )
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_exec_process", lambda *_, **__: "Command completed.")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    assert "CLI could not be located" in engine.install_native_sandbox()


def test_missing_sandbox_never_falls_back_to_local_execution(engine, monkeypatch):
    engine.SANDBOX_BACKEND = "auto"
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    monkeypatch.setattr(engine, "_docker_available", lambda: False)
    ran = []
    monkeypatch.setattr(engine, "_exec_process", lambda *_, **__: ran.append(1) or "ran locally")
    engine.grant_escalation("local_execution")
    out = engine._exec_sandbox_command("pwd")
    assert "sandbox is required" in out.lower()
    assert "ESCALATION_REQUEST" not in out
    assert ran == []


def test_auditor_absolute_artifact_paths_are_redirected_to_disposable_copy(
        engine, tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "library.py").write_text("print('ok')")
    engine.ARTIFACTS_ROOT = artifacts
    engine._sandbox_readonly = True
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *_args, **_kwargs: True)
    seen = {}

    def fake_native(command, _timeout, cwd, readonly=False):
        seen.update(command=command, cwd=cwd, readonly=readonly)
        return "ok"

    monkeypatch.setattr(engine, "_exec_native_sandbox", fake_native)

    result = engine._exec_sandbox_command(f"cd {artifacts} && python library.py")

    assert result == "ok"
    assert str(artifacts) not in seen["command"]
    assert seen["cwd"] != artifacts
    assert seen["readonly"] is True


@pytest.mark.parametrize("mode", ["edit", "full-auto"])
def test_unsandboxed_execution_is_refused_in_every_mode(engine, monkeypatch, mode):
    engine.SANDBOX_BACKEND = "auto"
    engine.PERMISSION_MODE = mode
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: None)
    monkeypatch.setattr(engine, "_docker_available", lambda: False)
    monkeypatch.setattr(engine, "_exec_process", lambda *_args, **_kwargs: "ran locally")

    out = engine._exec_sandbox_command("pwd")
    assert "sandbox is required" in out.lower()
    assert "ESCALATION_REQUEST" not in out


def test_sandbox_backend_setting_persists(engine, tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    monkeypatch.setattr(engine, "CONFIG_PATH", config)
    monkeypatch.setattr(engine, "APP_CONFIG", {})
    with pytest.raises(ValueError, match="auto, native, or docker"):
        engine.set_sandbox_backend("local")
    assert not config.exists()


def test_browser_missing_is_graceful(engine, monkeypatch):
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)
    monkeypatch.setattr(engine, "_ssrf_check", lambda _url: None)
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
