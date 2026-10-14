"""On the native Windows sandbox a shell command starts in the workspace it was given."""
import base64
import subprocess
import sys

import pytest


def test_the_bridge_starts_the_command_in_the_given_folder(engine, tmp_path):
    argv = engine._native_sandbox_shell_argv(
        f'"{sys.executable}" -c "import os; print(os.getcwd())"', cwd=tmp_path)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert done.stdout.strip().lower() == str(tmp_path).lower()


def test_without_a_folder_the_command_starts_where_the_agent_did(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    argv = engine._native_sandbox_shell_argv(f'"{sys.executable}" -c "import os; print(os.getcwd())"')
    assert len(argv) == 4
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert done.stdout.strip().lower() == str(tmp_path).lower()


@pytest.mark.skipif(sys.platform != "win32", reason="the native Windows branch")
@pytest.mark.parametrize("readonly,expect_cwd", [(False, True), (True, False)])
def test_the_native_runner_gets_the_workspace_as_the_start_folder(engine, tmp_path, monkeypatch, readonly, expect_cwd):
    seen = []
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["runner"])
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=0, **kw: seen.append(argv) or "ok")
    engine._exec_native_sandbox("dir", 5, tmp_path, readonly=readonly)
    argv = seen[0]
    bridge_args = argv[argv.index("-c") + 2:]
    assert len(bridge_args) == (2 if expect_cwd else 1)
    if expect_cwd:
        assert base64.b64decode(bridge_args[1]).decode("utf-8") == str(tmp_path.resolve())
