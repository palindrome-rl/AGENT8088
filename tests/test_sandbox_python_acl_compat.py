import os
import runpy
import pytest


def test_windows_private_temp_keeps_acl_inheritance_for_python_private_dirs(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows DSH ACL behavior")
    private = tmp_path / "dsh-private"
    outside = tmp_path / "workspace"
    private.mkdir()
    outside.mkdir()
    calls = []
    monkeypatch.setenv("AGENT8088_SANDBOX_TEMP", str(private))
    monkeypatch.setattr(os, "mkdir", lambda path, mode=0o777, **kwargs: calls.append((path, mode)))

    shim = os.path.join(os.path.dirname(__file__), "..", "src", "agent8088",
                        "sandbox_python", "sitecustomize.py")
    runpy.run_path(shim)
    os.mkdir(private / "pip-private-dir", 0o700)
    os.mkdir(os.fsencode(private / "pip-bytes-dir"), 0o700)
    os.mkdir(outside / "ordinary-private-dir", 0o700)
    os.mkdir(private / "ordinary-dir", 0o755)

    assert [mode for _, mode in calls] == [0o777, 0o777, 0o700, 0o755]
