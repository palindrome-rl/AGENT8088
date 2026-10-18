"""The native Windows sandbox blocks a program that starts a child process with piped output (Node's default).

The startup check passes either way, so a second probe records it, the capability registry shows it, and a
`spawn EPERM` result names the real cause instead of the file-write limit. Run on every platform by patching
`engine.sys.platform`.
"""
from types import SimpleNamespace as N

import pytest

from agent8088 import capabilities, engine


@pytest.fixture(autouse=True)
def clean_registry():
    capabilities.reset()
    yield
    capabilities.reset()


def _windows_with_runtime(monkeypatch, tmp_path, stdout, returncode=0, raises=None):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_native_sandbox_broken", False)
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: [r"C:\node\node.exe", r"C:\rt\runner.js"])
    monkeypatch.setattr(engine, "_native_sandbox_node_toolchain", lambda **k: (r"C:\node\node.exe", None, "v22"))
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        if raises:
            raise raises
        return N(stdout=stdout, stderr="", returncode=returncode)

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    return seen


def test_a_blocked_piped_spawn_is_recorded(monkeypatch, tmp_path):
    seen = _windows_with_runtime(monkeypatch, tmp_path, "agent8088-piped-spawn-blocked\n")
    engine._probe_piped_child_spawn(tmp_path)
    assert engine._native_piped_spawn_blocked is True
    assert seen["argv"][seen["argv"].index("--mode") + 1] == "workspace-write"
    assert "spawnSync" in seen["argv"][-1]


def test_a_working_piped_spawn_is_recorded_as_not_blocked(monkeypatch, tmp_path):
    _windows_with_runtime(monkeypatch, tmp_path, "agent8088-piped-spawn-ok\n")
    engine._probe_piped_child_spawn(tmp_path)
    assert engine._native_piped_spawn_blocked is False


def test_a_failed_outer_probe_cannot_claim_success(monkeypatch, tmp_path):
    _windows_with_runtime(monkeypatch, tmp_path, "agent8088-piped-spawn-ok\n", returncode=1)
    engine._probe_piped_child_spawn(tmp_path)
    assert engine._native_piped_spawn_blocked is None


def test_a_child_error_other_than_eperm_is_not_reported_as_working():
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    script = ("require('child_process').spawnSync = () => ({error: {code: 'ENOENT'}});"
              + engine._PIPED_SPAWN_SCRIPT)
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert "agent8088-piped-spawn-ok" not in result.stdout


def test_a_probe_that_cannot_run_leaves_the_answer_unknown(monkeypatch, tmp_path):
    _windows_with_runtime(monkeypatch, tmp_path, "", raises=OSError("no node"))
    engine._probe_piped_child_spawn(tmp_path)
    assert engine._native_piped_spawn_blocked is None
    _windows_with_runtime(monkeypatch, tmp_path, "something else entirely")
    engine._probe_piped_child_spawn(tmp_path)
    assert engine._native_piped_spawn_blocked is None


def test_nothing_is_probed_off_windows(monkeypatch, tmp_path):
    seen = _windows_with_runtime(monkeypatch, tmp_path, "agent8088-piped-spawn-blocked")
    monkeypatch.setattr(engine.sys, "platform", "linux")
    engine._probe_piped_child_spawn(tmp_path)
    assert engine._native_piped_spawn_blocked is None
    assert "argv" not in seen


def test_the_limit_shows_in_the_capability_registry_only_for_the_native_backend(monkeypatch):
    status = {"resolved": "native", "requested": "auto", "network_enforced": True}
    monkeypatch.setattr(engine, "_native_piped_spawn_blocked", True)
    engine._report_sandbox(status)
    entry = capabilities.get(capabilities.SANDBOX_CHILDREN)
    assert entry.state == capabilities.DEGRADED
    assert "spawn EPERM" in entry.impact and "sandbox_backend=docker" in entry.fix
    assert any(e.name == capabilities.SANDBOX_CHILDREN for e in capabilities.degraded())

    engine._report_sandbox({**status, "resolved": "docker"})
    assert capabilities.get(capabilities.SANDBOX_CHILDREN) is None

    monkeypatch.setattr(engine, "_native_piped_spawn_blocked", False)
    engine._report_sandbox(status)
    assert capabilities.get(capabilities.SANDBOX_CHILDREN) is None


def test_spawn_eperm_on_windows_names_the_real_cause(monkeypatch, tmp_path):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_native_piped_spawn_blocked", True)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    out = engine._explain_sandbox_denial("Error: spawn EPERM\n    at ChildProcess.spawn", tmp_path, "npm run build")
    assert "does not let a program start a child process with piped output" in out
    assert "not the file-write limit" in out
    assert "sandbox_backend=docker" in out
    assert "Shell commands may write only to" not in out


@pytest.mark.parametrize("backend, blocked", [("docker", True), ("native", False), ("native", None)])
def test_unconfirmed_or_docker_spawn_errors_are_not_diagnosed_as_native_pipes(monkeypatch, tmp_path, backend, blocked):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_native_piped_spawn_blocked", blocked)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: backend)
    result = engine._explain_sandbox_denial("Error: spawn EPERM", tmp_path, "node app.js")
    assert "does not let a program start a child process with piped output" not in result


def test_an_ordinary_denial_keeps_the_file_write_explanation(monkeypatch, tmp_path):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    out = engine._explain_sandbox_denial("Access is denied: C:\\Windows\\x.txt", tmp_path, "echo x > C:\\Windows\\x.txt")
    assert "Shell commands may write only to" in out
    assert "piped output" not in out


def test_spawn_eperm_off_windows_is_not_blamed_on_the_windows_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(engine.sys, "platform", "linux")
    out = engine._explain_sandbox_denial("Error: spawn EPERM", tmp_path, "node x.js")
    assert "piped output" not in out
