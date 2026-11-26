"""The `wsl` sandbox backend in the engine: when it is chosen, what one command looks like, its srt settings, the
fallback when it cannot start, and what the model is told. wsl.exe is never started here; the WSL attack tests in
test_sandbox_attacks.py run the real distro."""
import base64
import json
import sys
from pathlib import Path

import pytest

from agent8088 import wsl_sandbox as W


@pytest.fixture
def wsl(engine, tmp_path, monkeypatch):
    """A Windows engine whose WSL sandbox is set up (per its state file), with every other backend absent."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: data)
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    monkeypatch.setattr(engine, "SANDBOX_WSL", True)
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "SANDBOX_NETWORK_OPEN", False)
    monkeypatch.setattr(engine, "WSL_DISTRO", "agent8088-sandbox")
    monkeypatch.setattr(engine, "_wsl_sandbox_broken", False)
    monkeypatch.setattr(engine, "_wsl_sandbox_verified", None)
    monkeypatch.setattr(engine, "_wsl_sandbox_failure", "")
    monkeypatch.setattr(engine, "_native_sandbox_broken", False)
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: ["sandbox-runtime"])
    monkeypatch.setattr(engine, "_docker_usable", lambda: False)
    monkeypatch.setattr(engine, "_report_sandbox", lambda status=None: None)
    monkeypatch.setattr(W, "wsl_executable", lambda: r"C:\Windows\System32\wsl.exe")
    (data / "wsl-sandbox.json").write_text(json.dumps({
        "distro": "agent8088-sandbox", "setup": W.SETUP_VERSION, "runtime": engine.SANDBOX_RUNTIME_VERSION}))
    return data


def test_auto_picks_wsl_once_it_is_set_up(engine, wsl):
    assert engine._resolve_sandbox_backend() == "wsl"


def test_wsl_is_not_picked_without_its_state_file(engine, wsl):
    (wsl / "wsl-sandbox.json").unlink()
    assert engine._resolve_sandbox_backend() == "unavailable"


def test_a_setup_for_another_runtime_version_does_not_count(engine, wsl):
    (wsl / "wsl-sandbox.json").write_text(json.dumps({"distro": "agent8088-sandbox", "setup": W.SETUP_VERSION,
                                                      "runtime": "0.0.1"}))
    assert engine._resolve_sandbox_backend() != "wsl"


def test_wsl_off_or_open_network_keeps_the_old_order(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_WSL", False)
    assert engine._resolve_sandbox_backend() == "unavailable"
    monkeypatch.setattr(engine, "SANDBOX_WSL", True)
    monkeypatch.setattr(engine, "SANDBOX_NETWORK_OPEN", True)   # srt in WSL cannot open the network
    assert engine._resolve_sandbox_backend() != "wsl"


def test_a_broken_wsl_sandbox_hands_over_to_the_next_backend(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "_wsl_sandbox_broken", True)
    assert engine._resolve_sandbox_backend() == "native"


def test_asking_for_wsl_where_it_is_not_set_up_is_unavailable(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "wsl")
    (wsl / "wsl-sandbox.json").unlink()
    assert engine._resolve_sandbox_backend() == "unavailable"


def test_not_windows_never_uses_wsl(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "linux")
    assert not engine._wsl_sandbox_installed()


def test_status_names_the_distro_and_an_enforced_network(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine, "_wsl_sandbox_verified", True)
    status = engine.sandbox_status()
    assert status["resolved"] == "wsl" and status["verification"] == "verified"
    assert "agent8088-sandbox" in status["detail"]
    assert status["network_enforced"] is True and status["host_processes_shared"] is False


def test_set_sandbox_backend_accepts_wsl(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine, "update_simple_config", lambda path, values: None)
    monkeypatch.setattr(engine, "APP_CONFIG", {})
    assert engine.set_sandbox_backend("wsl")["resolved"] == "wsl"
    with pytest.raises(ValueError, match="wsl"):
        engine.set_sandbox_backend("wsl2")


def _capture(engine, monkeypatch):
    seen = {}

    def fake_exec(argv, timeout=25, shell=False, extra_env=None):
        seen.update(argv=argv, timeout=timeout, env=extra_env)
        return "ran"
    monkeypatch.setattr(engine, "_exec_process", fake_exec)
    return seen


@pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows paths")
def test_one_command_runs_as_agent_with_its_text_in_wslenv(engine, wsl, tmp_path, monkeypatch):
    seen = _capture(engine, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    out = engine._exec_wsl_sandbox(rf'python "{project}\app.py"', 30, cwd=project)
    assert out == "ran"
    assert seen["argv"] == W.command_argv(r"C:\Windows\System32\wsl.exe", "agent8088-sandbox")
    assert seen["timeout"] == 45   # the runner's own `timeout` (30 s) ends it inside the distro first
    env = seen["env"]
    assert set(env["WSLENV"].split(":")) == {W.ENV_COMMAND, W.ENV_SETTINGS, W.ENV_TIMEOUT}
    command = base64.b64decode(env[W.ENV_COMMAND]).decode()
    linux_project = W.to_linux(project.resolve())
    assert command.startswith(f"cd {linux_project} && export HOME=")
    assert f'python "{linux_project}/app.py"' in command
    assert "PIP_BREAK_SYSTEM_PACKAGES=1" in command
    settings = json.loads(Path(W.to_windows(env[W.ENV_SETTINGS])).read_text())
    assert linux_project in settings["filesystem"]["allowWrite"]
    assert not list((wsl / "wsl-commands").glob("*")) if (wsl / "wsl-commands").exists() else True


@pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows paths")
def test_settings_are_in_mnt_paths_and_hide_wsl_sockets(engine, wsl, tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".git" / "hooks").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setattr(engine.Path, "home", classmethod(lambda cls: home))
    data = engine._wsl_settings_data(False, project)
    fs = data["filesystem"]
    assert all(path.startswith("/mnt/") or path in ("/run/WSL", "/mnt/wslg", "/tmp/.X11-unix", W.TMP_DIR)
               for key in fs for path in fs[key])
    assert W.to_linux((home / ".ssh").resolve()) in fs["denyRead"]
    # A secret path that does not exist is left out: srt would skip it anyway, at 0.2 s a path over WSL's share.
    assert W.to_linux((home / ".aws").resolve()) not in fs["denyRead"]
    assert {"/run/WSL", "/mnt/wslg"} <= set(fs["denyRead"])
    assert W.to_linux((project / ".git").resolve()) in fs["denyWrite"]   # the repository metadata stays read-only
    # A write rule outside every writable folder is left out too: it is read-only there anyway.
    outside = [path for path in fs["denyWrite"] if path not in ("/run/WSL", "/mnt/wslg", "/tmp/.X11-unix")
               and not any(path == root or path.startswith(root + "/") for root in fs["allowWrite"])]
    assert not outside, outside
    assert data["network"]["allowLocalBinding"] is False
    service = engine._wsl_settings_data(False, project, service=True)
    assert service["network"]["allowAllUnixSockets"] is True and W.SERVICE_DIR in service["filesystem"]["allowWrite"]


def test_a_folder_off_any_drive_is_refused(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine.Path, "resolve", lambda self, strict=False: self)
    out = engine._wsl_sandbox_command("ls", Path(r"\\server\share\project"), False)
    assert isinstance(out, str) and "not on a drive letter" in out


def test_python_snippets_run_on_the_distros_python(engine):
    command = engine._wsl_python_command("print('a\\nb')")
    assert command.startswith('python3 -c "import base64;exec(')
    assert sys.executable not in command


def test_a_wsl_failure_before_the_command_ran_falls_back(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine, "_wsl_sandbox_ready", lambda cwd, readonly=False, quiet=False: True)
    monkeypatch.setattr(engine, "_exec_wsl_sandbox", lambda *a, **k: "There is no distribution with the supplied name.")
    monkeypatch.setattr(engine, "_native_sandbox_missing_requirements", lambda: [])
    monkeypatch.setattr(engine, "refresh_shell_grounding", lambda: False)
    monkeypatch.setattr(engine, "_native_sandbox_ready", lambda *a, **k: True)
    monkeypatch.setattr(engine, "_exec_native_sandbox", lambda command, timeout, cwd=None, readonly=False: "native ran")
    out = engine._run_in_sandbox_backend("wsl", "echo hi", 10, False, "", wsl)
    assert out == "native ran"
    assert engine._wsl_sandbox_broken


def test_a_command_that_ran_and_failed_is_not_retried(engine, wsl, monkeypatch):
    monkeypatch.setattr(engine, "_wsl_sandbox_ready", lambda cwd, readonly=False, quiet=False: True)
    monkeypatch.setattr(engine, "_exec_wsl_sandbox", lambda *a, **k: "boom\nCommand exited with status 1.")
    assert engine._run_in_sandbox_backend("wsl", "false", 10, False, "", wsl).startswith("boom")
    assert not engine._wsl_sandbox_broken


def test_the_shell_description_says_bash_in_wsl(engine, wsl, monkeypatch):
    specs = {"execute_shell": {"description": "Run a command."}}
    monkeypatch.setattr(engine, "_SHELL_GROUNDING", {"cwd": None, "suffix": ""})
    # The folder probe looks up programs; with sys.platform faked on Linux that would take Windows-only paths.
    monkeypatch.setattr(engine, "_environment_probe", lambda cwd: "holds a project")
    monkeypatch.setattr(engine, "_probe_interpreters", lambda: ([], []))
    engine._ground_execute_shell_description(specs)
    text = specs["execute_shell"]["description"]
    assert "bash in Agent8088's WSL sandbox" in text and "/mnt/e" in text and "python3" in text
    assert "command -v" in text and "PowerShell/cmd.exe" not in text.split("using", 1)[1].split(".", 1)[0]


def test_a_mnt_path_from_the_shell_is_the_windows_path_for_the_file_tools(engine, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    assert engine._from_container_path("/mnt/e/proj/app.py") == r"E:\proj\app.py"
    assert engine._from_container_path("/mnt/e") == "E:\\"


def test_uninstall_removes_only_the_distro_setup_made(tmp_path, monkeypatch):
    from agent8088 import cli
    removed = []
    monkeypatch.setattr(W, "remove", lambda distro, wsl=None, run=None: removed.append(distro))
    assert cli._remove_agent8088_wsl_distro(tmp_path) == ""   # no state file: nothing to remove
    (tmp_path / "wsl-sandbox.json").write_text(json.dumps({"distro": "agent8088-sandbox"}))
    assert cli._remove_agent8088_wsl_distro(tmp_path) == "agent8088-sandbox"
    assert removed == ["agent8088-sandbox"]
