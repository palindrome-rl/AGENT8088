"""A native sandbox that cannot start falls back to Docker.

`auto` documents "native OS isolation first, then Docker when available", but the
choice was only made at selection time, from the presence of the runtime binary.
On Windows the runtime can be installed and still fail to start a sandbox — it
runs commands as a restricted account and could not log into it — so the command
came back refused while a working Docker sat idle. The agent then reported it
could not verify its work and printed an invented "expected output" table.

The retry is deliberately limited to PRE-FLIGHT failures. A command that ran and
failed must never be retried on another backend: it may already have had effects.
"""
from types import SimpleNamespace

PREFLIGHT = (
    "Error: WFP egress fence could not be verified — `srt-win wfp verify` exited 1 "
    'with unparseable output "" (stderr: "srt-win: error: spawn runner for egress '
    "probe: CreateProcessWithLogonW(srt-sandbox): Access is denied. (0x80070005) — "
    'ensure the Secondary Logon service (seclogon) is running.")'
)


def test_preflight_failures_are_recognised(engine):
    assert engine._native_sandbox_unusable(PREFLIGHT) is True
    assert engine._native_sandbox_unusable("Native sandbox runtime is unavailable.") is True


def test_a_command_that_ran_and_failed_is_not_a_preflight_failure(engine):
    """The critical distinction: this one must never be retried elsewhere."""
    assert engine._native_sandbox_unusable(
        "Traceback (most recent call last):\nValueError: bad\n"
        "Command exited with status 1.") is False
    assert engine._native_sandbox_unusable("hello\n") is False


def test_command_output_mentioning_error_is_not_a_preflight_failure(engine):
    """A build log is not an excuse to run the build again."""
    assert engine._native_sandbox_unusable(
        "Error: 0 warnings, 0 errors\nCommand exited with status 0.") is False


def _wire(engine, monkeypatch, native_result, docker_available=True):
    calls = []
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    monkeypatch.setattr(engine, "_docker_available", lambda: docker_available)
    monkeypatch.setattr(
        engine, "_exec_native_sandbox",
        lambda *a, **k: calls.append("native") or native_result)
    monkeypatch.setattr(
        engine, "_exec_docker_command",
        lambda *a, **k: calls.append("docker") or "ran in docker")
    return calls


def test_unusable_native_falls_back_to_docker(engine, monkeypatch):
    calls = _wire(engine, monkeypatch, PREFLIGHT)
    assert engine._exec_sandbox_command("python demo.py") == "ran in docker"
    assert calls == ["native", "docker"]


def test_working_native_does_not_touch_docker(engine, monkeypatch):
    calls = _wire(engine, monkeypatch, "hello from native")
    assert engine._exec_sandbox_command("echo hello") == "hello from native"
    assert calls == ["native"], "a healthy native run must not be repeated"


def test_a_failing_command_is_not_re_run_on_docker(engine, monkeypatch):
    """Re-running it would repeat any side effects it already had."""
    failed = "wrote half a file\nCommand exited with status 1."
    calls = _wire(engine, monkeypatch, failed)
    assert engine._exec_sandbox_command("python demo.py") == failed
    assert calls == ["native"], "command failure is a result, not an infrastructure fault"


def test_without_docker_the_runtime_error_is_kept(engine, monkeypatch):
    """The runtime's message names the cause; a generic one would lose it."""
    calls = _wire(engine, monkeypatch, PREFLIGHT, docker_available=False)
    result = engine._exec_sandbox_command("python demo.py")
    assert calls == ["native"]
    assert "Secondary Logon" in result, "the actionable detail must survive"


def test_a_broken_native_runtime_is_only_attempted_once(engine, monkeypatch):
    """It does not heal between commands, so retrying costs a subprocess to
    reach the same failure — and reprints its stderr above every command."""
    calls = _wire(engine, monkeypatch, PREFLIGHT)

    engine._exec_sandbox_command("echo one")
    engine._exec_sandbox_command("echo two")
    engine._exec_sandbox_command("echo three")

    assert calls == ["native", "docker", "docker", "docker"], (
        "native should be tried once, then skipped for the session")


def test_the_fallback_warns_once_not_per_command(engine, monkeypatch, caplog):
    _wire(engine, monkeypatch, PREFLIGHT)

    with caplog.at_level("WARNING"):
        engine._exec_sandbox_command("echo one")
        engine._exec_sandbox_command("echo two")

    warnings = [r for r in caplog.records if "native sandbox could not start" in r.message]
    assert len(warnings) == 1, f"expected one warning, got {len(warnings)}"


def test_a_healthy_native_runtime_is_never_marked_broken(engine, monkeypatch):
    calls = _wire(engine, monkeypatch, "hello from native")

    engine._exec_sandbox_command("echo one")
    engine._exec_sandbox_command("echo two")

    assert calls == ["native", "native"], "a working runtime must keep being used"
    assert engine._native_sandbox_broken is False


def test_python_code_still_reaches_docker_as_python(engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_exec_native_sandbox", lambda *a, **k: PREFLIGHT)
    monkeypatch.setattr(
        engine, "_exec_docker_command",
        lambda command, timeout, python_code=False, image="", workspace=None:
            seen.update(command=command, python_code=python_code) or "ok")

    engine._exec_sandbox_command("print(1)", python_code=True)

    assert seen["python_code"] is True, "run_sandboxed must stay Python on the fallback"
    assert seen["command"] == "print(1)", "docker gets the raw code, not the native wrapper"


def test_structured_git_fallback_keeps_the_pinned_git_image(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["node", "srt.js"])
    monkeypatch.setattr(engine, "_write_sandbox_settings", lambda **_kwargs: "settings.json")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: engine.PROJECT_ROOT)
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda *_args, **_kwargs: calls.append("native") or PREFLIGHT,
    )
    monkeypatch.setattr(
        engine, "_exec_docker_command",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "ran in docker",
    )

    assert engine._exec_sandbox_argv(["git", "status"]) == "ran in docker"
    _, docker = calls
    assert docker[1]["image"] == engine.GIT_DOCKER_IMAGE
    assert docker[1]["workspace"] == engine.PROJECT_ROOT
    assert docker[1]["readonly"] is True


def test_status_reports_docker_after_native_preflight_failure(engine, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["node", "srt.js"])
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_native_sandbox_broken", True)

    assert engine.sandbox_status()["resolved"] == "docker"


def test_windows_docker_probe_prefers_the_executable(engine, monkeypatch):
    unix_shim = r"C:\Program Files\Docker\Docker\resources\bin\docker"
    windows_exe = unix_shim + ".exe"
    seen = []
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(
        engine.shutil, "which",
        lambda name: {"docker": unix_shim, "docker.exe": windows_exe}.get(name),
    )
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda argv, **_kwargs: seen.append(argv) or SimpleNamespace(returncode=0),
    )

    assert engine._docker_available() is True
    assert seen == [[windows_exe, "info"]]
