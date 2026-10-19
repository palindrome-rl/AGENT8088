"""A file the workspace happens to hold must not end the request when the read-only sandbox copies it."""
import shutil
import os
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT8088_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT8088_CONFIG", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("AGENT8088_DOCKER_AUTOSTART", "0")
    from agent8088 import engine as runtime
    return importlib.reload(runtime)


def test_windows_device_names_are_recognised(engine):
    for name in ("nul", "NUL", "nul.txt", "Con", "aux.log", "com1", "LPT9", "prn"):
        assert engine._is_windows_device_name(name), name
    for name in ("null", "console.js", "auxiliary.py", "com10", "lpt0", "server.py", "nulled.txt"):
        assert not engine._is_windows_device_name(name), name


def test_the_copy_ignore_skips_device_names_and_keeps_the_usual_patterns(engine, monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "win32")
    ignore = engine._audit_copy_ignore(shutil.ignore_patterns("*.pem", "node_modules"))
    skipped = ignore("anywhere", ["nul", "NUL.txt", "app.py", "key.pem", "node_modules", "con"])
    assert skipped == {"nul", "NUL.txt", "key.pem", "node_modules", "con"}


def test_the_ignore_callback_works_inside_copytree(engine, tmp_path):
    """Device names cannot be created as files on Windows, so copy a tree and check the other patterns survive."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "app.py").write_text("print(1)", encoding="utf-8")
    (src / "key.pem").write_text("secret", encoding="utf-8")
    shutil.copytree(src, dst, ignore=engine._audit_copy_ignore(shutil.ignore_patterns("*.pem")))
    assert (dst / "app.py").exists() and not (dst / "key.pem").exists()


def test_a_failed_workspace_copy_is_a_tool_error_not_an_exception(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_sandbox_readonly", True)

    def refuse(*_args, **_kwargs):
        raise shutil.Error([("a", "b", "are the same file")])

    ran = []
    monkeypatch.setattr(engine.shutil, "copytree", refuse)
    monkeypatch.setattr(engine, "_run_in_sandbox_backend", lambda *a, **k: ran.append(a) or "ran")

    result = engine._run_sandbox_command("ls", 10)

    assert result.startswith("Error: could not copy the workspace for the read-only sandbox")
    assert "Nothing was run" in result
    assert not ran


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_copy_keeps_legal_application_modules(engine, monkeypatch, platform):
    monkeypatch.setattr(engine.sys, "platform", platform)
    ignore = engine._audit_copy_ignore(shutil.ignore_patterns("*.pem", "node_modules"))
    assert ignore("workspace", ["aux.py", "con.py", "com1.py", "app.py", "key.pem"]) == {"key.pem"}


@pytest.mark.parametrize("copy_fails", [True, False])
def test_cleanup_failure_does_not_discard_the_tool_result(engine, monkeypatch, tmp_path, copy_fails):
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_sandbox_readonly", True)
    monkeypatch.setattr(engine, "_artifact_workspace_command", lambda command: command)
    def cleanup():
        raise PermissionError("temporary file held open")
    monkeypatch.setattr(engine.tempfile, "TemporaryDirectory", lambda **kwargs:
                        SimpleNamespace(name=str(tmp_path / "copy"), cleanup=cleanup))
    def copy(*args, **kwargs):
        if copy_fails:
            raise shutil.SameFileError("copy refused")
    monkeypatch.setattr(engine.shutil, "copytree", copy)
    ran = []
    monkeypatch.setattr(engine, "_run_in_sandbox_backend", lambda *a, **k: ran.append(a) or "checked")
    monkeypatch.setattr(engine, "_explain_sandbox_denial", lambda result, *args: result)
    result = engine._run_sandbox_command("echo test")
    if copy_fails:
        assert result.startswith("Error: could not copy the workspace")
        assert "Nothing was run" in result and not ran
    else:
        assert result.startswith("checked") and len(ran) == 1
    assert "could not remove" in result.lower()
    assert str(tmp_path / "copy") in result


@pytest.mark.skipif(os.name != "nt", reason="Windows extended paths")
def test_real_windows_device_named_file_is_skipped(engine, tmp_path):
    src, dst = tmp_path / "source", tmp_path / "destination"
    src.mkdir()
    (src / "app.py").write_text("print('ok')", encoding="utf-8")
    # Extended paths can create the real filename that a Unix-style shell leaves.
    device_file = Path("\\\\?\\" + str(src.resolve() / "nul"))
    device_file.write_text("discarded shell output", encoding="utf-8")
    try:
        assert "nul" in os.listdir(src)
        shutil.copytree(src, dst, ignore=engine._audit_copy_ignore(shutil.ignore_patterns("*.pem")))
        assert os.listdir(dst) == ["app.py"]
    finally:
        device_file.unlink()
