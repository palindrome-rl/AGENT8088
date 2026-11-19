"""Git for Windows' MSYS tools (head, find, timeout, ...) die in the Windows sandbox with
"couldn't create signal pipe, Win32 error 5". The sandboxed shell's PATH drops that folder."""
import os
import subprocess
import sys

import pytest

from agent8088 import engine

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe bridge")


def _path_seen_by_command(path):
    argv = engine._native_sandbox_shell_argv("echo %PATH%")
    env = dict(os.environ, PATH=path)
    out = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=30).stdout.strip()
    return out.split(os.pathsep)


def test_drops_git_usr_bin_and_keeps_the_rest():
    path = os.pathsep.join([r"C:\Program Files\Git\cmd", r"C:\Program Files\Git\usr\bin",
                            r"C:\Program Files\Git\mingw64\bin", r"C:\Windows\System32"])
    seen = _path_seen_by_command(path)
    assert r"C:\Program Files\Git\usr\bin" not in seen
    for kept in (r"C:\Program Files\Git\cmd", r"C:\Program Files\Git\mingw64\bin", r"C:\Windows\System32"):
        assert kept in seen


def test_matches_case_slashes_and_trailing_separator():
    path = os.pathsep.join([r"c:\git\USR\BIN" + "\\", "D:/Tools/Git/usr/bin", r"C:\Other\usr\bin", r"C:\Windows"])
    seen = _path_seen_by_command(path)
    assert r"C:\Other\usr\bin" in seen and r"C:\Windows" in seen
    assert not any(p.lower().replace("/", "\\").rstrip("\\").endswith("git\\usr\\bin") for p in seen)


def test_path_without_git_is_unchanged():
    path = os.pathsep.join([r"C:\Windows", r"C:\Python313\Scripts"])
    seen = _path_seen_by_command(path)
    assert r"C:\Windows" in seen and r"C:\Python313\Scripts" in seen
