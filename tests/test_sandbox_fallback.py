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

import pytest

PREFLIGHT = (
    "Error: WFP egress fence could not be verified — `srt-win wfp verify` exited 1 "
    'with unparseable output "" (stderr: "srt-win: error: spawn runner for egress '
    "probe: CreateProcessWithLogonW(srt-sandbox): Access is denied. (0x80070005) — "
    'ensure the Secondary Logon service (seclogon) is running.")'
)


def test_preflight_failures_are_recognised(engine):
    assert engine._native_sandbox_unusable(PREFLIGHT) is True
    assert engine._native_sandbox_unusable("Native sandbox runtime is unavailable.") is True


def test_linux_and_macos_preflight_failures_are_recognised(engine):
    for failure in (
        "bwrap: No permissions to create new namespace",
        "bwrap: Creating new namespace failed",
        "bwrap: Can't mount proc",
        "apply-seccomp: Operation not permitted",
        "sandbox-exec: sandbox_init: Operation not permitted",
        "sandbox-exec: sandbox_apply: Operation not permitted",
    ):
        assert engine._native_sandbox_unusable(failure) is True


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
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *_args, **_kwargs: True)
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


def test_without_docker_hides_the_runtime_error(engine, monkeypatch):
    """Infrastructure diagnostics must not be presented as command output."""
    calls = _wire(engine, monkeypatch, PREFLIGHT, docker_available=False)
    result = engine._exec_sandbox_command("python demo.py")
    assert calls == ["native"]
    assert "sandbox is required" in result.lower()
    assert "Secondary Logon" not in result


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
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *_args, **_kwargs: True)
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
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *_args, **_kwargs: True)
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


def test_native_probe_is_cached_after_a_success(engine, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["srt"])
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_write_sandbox_settings", lambda *_args: tmp_path / "settings.json")
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert engine._native_sandbox_ready(tmp_path / "workspace") is True
    assert engine._native_sandbox_ready(tmp_path / "workspace") is True
    assert len(calls) == 1
    assert engine.native_sandbox_verified() is True


def test_failed_native_probe_latches_before_any_user_command(engine, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["srt"])
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path)
    monkeypatch.setattr(engine, "_write_sandbox_settings", lambda *_args: tmp_path / "settings.json")
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(
            returncode=1, stdout="", stderr="bwrap: Creating new namespace failed"),
    )

    assert engine._native_sandbox_ready(tmp_path / "workspace") is False
    assert engine._native_sandbox_ready(tmp_path / "workspace") is False
    assert len(calls) == 1
    assert engine._native_sandbox_broken is True


def test_native_probe_latches_a_workspace_preparation_failure(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["srt"])
    monkeypatch.setattr(engine, "_write_sandbox_settings",
                        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")))

    assert engine._native_sandbox_ready(tmp_path / "workspace") is False
    assert engine._native_sandbox_broken is True


def test_docker_creates_a_fresh_workspace_before_mounting(engine, tmp_path, monkeypatch):
    workspace = tmp_path / "fresh-workspace"
    seen = {}
    monkeypatch.setattr(engine, "_running_in_container", lambda: False)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda _image: "")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-data")
    monkeypatch.setattr(
        engine, "_exec_process",
        lambda argv, **_kwargs: seen.update(argv=argv) or "ok",
    )

    assert engine._exec_docker_command("echo ok", 10, workspace=workspace) == "ok"
    assert workspace.is_dir()
    assert f"type=bind,src={workspace.resolve()},dst=/workspace" in seen["argv"]


def test_docker_container_and_daemon_failures_are_not_command_output(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda _image: "")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-data")
    monkeypatch.setattr(engine, "_exec_process", lambda *_args, **_kwargs: "Cannot connect to the Docker daemon")
    result = engine._exec_docker_command("echo ok", 10, workspace=tmp_path / "workspace")
    assert "Cannot connect" not in result
    assert "Docker sandbox is unavailable" in result


def test_running_in_a_container_does_not_by_itself_refuse_docker(engine, tmp_path, monkeypatch):
    """Being in a container is not the question; whether the mount resolves is.

    Refusing on /.dockerenv alone broke the one configuration where
    docker-in-docker does work — the project mounted at the same absolute path
    inside and outside — and told the operator the workspace was not
    host-visible when it demonstrably was.
    """
    workspace = tmp_path / "shared"
    monkeypatch.setattr(engine, "_running_in_container", lambda: True)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda _image: "")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-data")
    monkeypatch.setattr(engine, "_exec_process", lambda *_args, **_kwargs: "mounted fine")

    assert engine._exec_docker_command("echo ok", 10, workspace=workspace) == "mounted fine"
    assert engine._docker_sandbox_broken is False


def test_an_unmountable_workspace_is_diagnosed_and_latched(engine, tmp_path, monkeypatch):
    daemon_refusal = ('docker: Error response from daemon: invalid mount config for '
                      'type "bind": bind source path does not exist: /work/artifacts')
    monkeypatch.setattr(engine, "_running_in_container", lambda: True)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda _image: "")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent-data")
    monkeypatch.setattr(engine, "_exec_process", lambda *_args, **_kwargs: daemon_refusal)

    result = engine._exec_docker_command("echo ok", 10, workspace=tmp_path / "ws")
    # The daemon's own words must not reach the model as command output.
    assert "Error response from daemon" not in result
    assert "same absolute path" in result
    assert "identical path" in result  # the in-container remedy
    assert engine._docker_sandbox_broken is True
    # Latched: docker is out of the running everywhere, not retried per call.
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    assert engine._docker_usable() is False


def test_a_latched_docker_failure_makes_the_backend_unavailable(engine, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "docker")
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    assert engine._resolve_sandbox_backend() == "docker"

    engine._mark_docker_sandbox_broken("bind source path does not exist: /work")
    assert engine._resolve_sandbox_backend() == "unavailable"


def test_no_sandbox_refusal_names_the_real_obstacle(engine, monkeypatch):
    """"Install and start Docker" is wrong when Docker is running and mounted wrong.

    The refusal is the only thing the operator and the model get to read, so it
    has to carry what the probes learned rather than a generic remedy.
    """
    generic = engine._sandbox_required_error()
    assert "install and start Docker" in generic

    engine._mark_native_sandbox_broken("bwrap: No permissions to create new namespace")
    engine._mark_docker_sandbox_broken(
        'invalid mount config for type "bind": bind source path does not exist: /work')
    detailed = engine._sandbox_required_error()
    assert "same absolute path" in detailed
    assert "install and start Docker" not in detailed

    monkeypatch.setattr(engine, "_docker_available", lambda: False)
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    status = engine.sandbox_status()
    assert status["resolved"] == "unavailable"
    assert "same absolute path" in status["detail"]


def test_status_verification_describes_docker_when_docker_is_active(engine, monkeypatch):
    """A docker-backed session showed native's verdict, which said nothing."""
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "docker")
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_docker_sandbox_verified", True)
    monkeypatch.setattr(engine, "_native_sandbox_broken", True)

    status = engine.sandbox_status()
    assert status["resolved"] == "docker"
    assert status["verification"] == "verified"


def test_docker_probe_is_cached_per_workspace(engine, tmp_path, monkeypatch):
    """artifacts/ and the project root are mounted separately; one can fail."""
    calls = []
    engine._docker_images_present.add(engine.DOCKER_IMAGE)
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda argv, **_kwargs: calls.append(argv) or SimpleNamespace(
            returncode=0, stdout="", stderr=""),
    )

    first, second = tmp_path / "a", tmp_path / "b"
    assert engine._docker_sandbox_ready(first) is True
    assert engine._docker_sandbox_ready(first) is True  # served from cache
    assert len(calls) == 1
    assert engine._docker_sandbox_ready(second) is True
    assert len(calls) == 2


def test_docker_probe_does_not_pull_a_missing_image(engine, tmp_path, monkeypatch):
    """A startup probe must never trigger a 300s download."""
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_exec_process",
                        lambda *_args, **_kwargs: "exited with status 1")
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("probe ran a container without the image"),
    )

    assert engine._docker_sandbox_ready(tmp_path / "ws") is False
    assert engine._docker_sandbox_broken is False  # unverified, not broken


def test_startup_verification_prefers_native_and_never_probes_docker(engine, tmp_path, monkeypatch):
    """Native first is the design: a healthy machine must not pay for docker."""
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(engine, "_native_sandbox_verified", True)
    monkeypatch.setattr(
        engine, "_docker_sandbox_ready",
        lambda *_a, **_k: pytest.fail("docker probed while native was healthy"),
    )
    monkeypatch.setattr(
        engine, "_docker_available",
        lambda: pytest.fail("docker probed while native was healthy"),
    )

    assert engine.verify_sandbox_backend()["resolved"] == "native"


def test_startup_verification_probes_docker_only_after_native_fails(engine, tmp_path, monkeypatch):
    probed = []
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["srt"])
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_docker_sandbox_ready",
                        lambda *_a, **_k: probed.append(True) or True)
    monkeypatch.setattr(
        engine.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="",
            stderr="bwrap: No permissions to create new namespace"),
    )

    status = engine.verify_sandbox_backend()
    assert probed == [True]
    assert status["resolved"] == "docker"


def test_startup_verification_with_docker_requested_skips_the_native_probe(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "docker")
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_docker_sandbox_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(
        engine, "_native_sandbox_ready",
        lambda *_a, **_k: pytest.fail("native probed when docker was requested"),
    )

    assert engine.verify_sandbox_backend()["resolved"] == "docker"


def test_status_reports_docker_after_native_preflight_failure(engine, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["node", "srt.js"])
    monkeypatch.setattr(engine, "_docker_available", lambda: True)
    monkeypatch.setattr(engine, "_native_sandbox_broken", True)

    status = engine.sandbox_status()
    assert status["resolved"] == "docker"
    # `verification` describes the backend that would actually run, so with
    # docker active it reports on docker — which has not been probed yet here.
    # Native's failure is the reason we are on docker, so it stays in `detail`
    # rather than being dropped.
    assert status["verification"] == "unverified"
    assert "native failed verification" in status["detail"]


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
