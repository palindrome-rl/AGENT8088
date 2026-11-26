"""Temporary files in the WSL sandbox live on the distro's own filesystem, not on the Windows drive.

Seen in a benchmark run: TMPDIR pointed at a /mnt/c/... folder. Python's TemporaryFile deletes the file while it is
still open, which a Windows drive does not support, so every pytest run crashed at teardown with FileNotFoundError
in its output capture, and correct work could not be tested. A folder in the distro's /tmp works.
"""
import base64
import sys

import pytest

from agent8088 import wsl_sandbox as W


def test_the_temp_folder_is_in_the_distro():
    assert W.TMP_DIR.startswith("/tmp/") and not W.TMP_DIR.startswith("/mnt/")


def test_the_runner_makes_it_private_before_srt_starts():
    script = W.RUNNER_SCRIPT
    make = f"install -d -m 700 {W.TMP_DIR}"
    assert make in script
    assert script.index(make) < script.index("node ")


def test_a_distro_with_the_older_runner_is_set_up_again():
    assert W.SETUP_VERSION not in ("1", "2")


def test_the_sandbox_may_write_there(engine, tmp_path):
    for readonly in (False, True):
        assert W.TMP_DIR in engine._wsl_settings_data(readonly, tmp_path)["filesystem"]["allowWrite"]


@pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows paths")
def test_commands_get_it_as_tmpdir(engine, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(W, "wsl_executable", lambda: r"C:\Windows\System32\wsl.exe")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_exec_process",
                        lambda argv, timeout, extra_env: seen.update(env=extra_env) or "ran")
    project = tmp_path / "project"
    project.mkdir()
    engine._exec_wsl_sandbox("python -m pytest -q", 30, cwd=project)
    env = seen["env"]
    command = (base64.b64decode(env[W.ENV_COMMAND]).decode() if env.get(W.ENV_COMMAND)
               else open(W.to_windows(env[W.ENV_COMMAND_FILE]), encoding="utf-8").read())
    assert f"TMPDIR={W.TMP_DIR}" in command
