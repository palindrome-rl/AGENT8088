"""install_native_sandbox() provisions the DSH Windows ACL package on win32
instead of @anthropic-ai/sandbox-runtime + the old `windows-install` step
(srt-sandbox account provisioning), which is what made native sandbox setup
require an elevated terminal and fail with ERROR_ACCESS_DENIED on Windows.
"""
from agent8088 import engine


def test_windows_install_runs_npm_for_dsh_package(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: {"node": "node", "npm": "npm"}.get(name))
    monkeypatch.setattr(engine.subprocess, "run",
                         lambda *a, **k: type("R", (), {"stdout": "v20.11.0\n"})())
    calls = []

    def fake_exec_process(argv, timeout):
        calls.append(argv)
        return "ok"

    monkeypatch.setattr(engine, "_exec_process", fake_exec_process)
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *a, **k: True)

    result = engine.install_native_sandbox()

    assert len(calls) == 1
    npm_call = calls[0]
    assert any(f"@deepseek-ai/dsh-sandbox-windows-acl@{engine._DSH_SANDBOX_ACL_VERSION}" in part
               for part in npm_call)
    assert not any("windows-install" in str(part) for call in calls for part in call)
    assert not any("@anthropic-ai/sandbox-runtime" in str(part) for call in calls for part in call)
    assert "verified" in result


def test_non_windows_install_still_uses_anthropic_sandbox_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: {"node": "node", "npm": "npm"}.get(name))
    monkeypatch.setattr(engine.subprocess, "run",
                         lambda *a, **k: type("R", (), {"stdout": "v20.11.0\n"})())
    calls = []
    monkeypatch.setattr(engine, "_exec_process",
                         lambda argv, timeout: calls.append(argv) or "ok")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *a, **k: True)

    engine.install_native_sandbox()

    npm_call = calls[0]
    assert any(f"@anthropic-ai/sandbox-runtime@{engine.SANDBOX_RUNTIME_VERSION}" in part
               for part in npm_call)


def test_windows_install_reports_missing_requirements(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: {"node": "node", "npm": "npm"}.get(name))
    monkeypatch.setattr(engine.subprocess, "run",
                         lambda *a, **k: type("R", (), {"stdout": "v20.11.0\n"})())
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout: "ok")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements",
                         lambda: ["koffi native addon"])

    result = engine.install_native_sandbox()

    assert "koffi native addon" in result


def test_windows_install_retries_verification_after_transient_failure(tmp_path, monkeypatch):
    """A freshly-written koffi.node can still be mid antivirus-scan when the
    first probe runs. That first failure must not be reported as a permanent
    one if a retry moments later succeeds.
    """
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: {"node": "node", "npm": "npm"}.get(name))
    monkeypatch.setattr(engine.subprocess, "run",
                         lambda *a, **k: type("R", (), {"stdout": "v20.11.0\n"})())
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout: "ok")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine.time, "sleep", lambda secs: None)

    attempts = []

    def flaky_ready(*a, **k):
        attempts.append(1)
        return len(attempts) > 1

    monkeypatch.setattr(engine, "_native_sandbox_ready", flaky_ready)

    result = engine.install_native_sandbox()

    assert len(attempts) == 2
    assert "verified" in result
    assert "could not" not in result


def test_windows_install_reports_failure_after_retry_also_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(engine, "_which_executable",
                         lambda name: {"node": "node", "npm": "npm"}.get(name))
    monkeypatch.setattr(engine.subprocess, "run",
                         lambda *a, **k: type("R", (), {"stdout": "v20.11.0\n"})())
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout: "ok")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine.time, "sleep", lambda secs: None)

    attempts = []

    def always_fails(*a, **k):
        attempts.append(1)
        engine._native_sandbox_failure = "windows-acl-run: boom"
        return False

    monkeypatch.setattr(engine, "_native_sandbox_ready", always_fails)

    result = engine.install_native_sandbox()

    assert len(attempts) == 2
    assert "could not" in result
