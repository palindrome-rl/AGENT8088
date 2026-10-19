"""A file the workspace happens to hold must not end the request when the read-only sandbox copies it."""
import shutil


def test_windows_device_names_are_recognised(engine):
    for name in ("nul", "NUL", "nul.txt", "Con", "aux.log", "com1", "LPT9", "prn"):
        assert engine._is_windows_device_name(name), name
    for name in ("null", "console.js", "auxiliary.py", "com10", "lpt0", "server.py", "nulled.txt"):
        assert not engine._is_windows_device_name(name), name


def test_the_copy_ignore_skips_device_names_and_keeps_the_usual_patterns(engine):
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
