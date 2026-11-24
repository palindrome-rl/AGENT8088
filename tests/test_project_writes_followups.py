"""Follow-ups to letting shell commands write the project folder.

- Repositories nested in the project (submodules, vendored checkouts) have their own .git and hooks: they are read-only
  to shell commands too, on every backend.
- A smoke test runs on a copy of the shell's folder. With the shell in the project, a large project was too large to
  copy and its scripts got an error instead of a run: the script's own folder is copied then.
- On macOS and Linux the smoke test started with `cd <the script's path>`, which fails for any script: it now goes to
  the script's folder.
"""
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def project(engine, tmp_path, monkeypatch):
    root = tmp_path / "work"
    for git in (root / ".git", root / "vendor" / "lib" / ".git", root / "node_modules" / "pkg" / ".git"):
        (git / "hooks").mkdir(parents=True)
        (git / "config").write_text("[core]\n", encoding="utf-8")
    (root / "sub" / "worktree").mkdir(parents=True)
    (root / "sub" / "worktree" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_nested_repo_cache", {})
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_secret_scan_roots", lambda workspace: [])
    return root


def test_nested_repositories_are_found(engine, project):
    found = {p.relative_to(project.resolve()).as_posix() for p in engine._protected_repo_paths(project)}
    assert found == {".git", "vendor/lib/.git", "sub/worktree/.git"}   # not inside node_modules


def test_srt_denies_writes_to_every_repository(engine, project):
    deny = engine._sandbox_settings_data(False, project)["filesystem"]["denyWrite"]
    for rel in (".git", "vendor/lib/.git", "sub/worktree/.git"):
        assert str((project / rel).resolve()) in deny


def test_docker_mounts_each_repository_read_only_where_it_is(engine, project, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_docker_sandbox_broken", False)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    monkeypatch.setattr(engine, "_docker_egress_args", lambda: ("none", []))
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=25: seen.setdefault("argv", argv) and "ok")
    engine._exec_docker_command("ls", 10, workspace=project)
    mounts = " ".join(seen["argv"])
    assert "dst=/workspace/.git,readonly" in mounts and "dst=/workspace/vendor/lib/.git,readonly" in mounts


def test_the_windows_guard_puts_back_a_nested_hook(engine, project, monkeypatch):
    hook = project / "vendor" / "lib" / ".git" / "hooks" / "post-checkout"

    def plant(argv, timeout=25):
        hook.write_text("evil\n", encoding="utf-8")
        return "done"
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_native_sandbox_argv", lambda: ["runner"])
    monkeypatch.setattr(engine, "_native_sandbox_node_toolchain", lambda require_npm=False: (None, None, None))
    monkeypatch.setattr(engine, "_exec_process", plant)
    out = engine._exec_native_sandbox("echo", 10, cwd=project)
    assert not hook.exists() and "were put back" in out and "hooks/post-checkout" in out


def test_the_search_is_bounded(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_nested_repo_cache", {})
    monkeypatch.setattr(engine, "_NESTED_REPO_MAX", 2)
    for n in range(5):
        (tmp_path / f"r{n}" / ".git").mkdir(parents=True)
    assert len(engine._protected_repo_paths(tmp_path)) == 2


@pytest.fixture
def smoke(engine, tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "tools").mkdir(parents=True)
    script = root / "tools" / "report.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    seen = {}

    def fake_run(command, timeout=25, python_code=False, image=""):
        seen.update(command=command, source=engine._readonly_copy_source, readonly=engine._sandbox_readonly)
        return "ok"
    monkeypatch.setattr(engine, "_shell_workspace_root", root)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_run_sandbox_command", fake_run)
    return root, script, seen


def test_the_smoke_test_starts_in_the_scripts_folder(engine, smoke):
    root, script, seen = smoke
    engine._smoke_script_runner(script)("python report.py", 10)
    assert seen["command"] == "cd tools && python report.py" and seen["readonly"] is True
    assert Path(seen["source"]) == root.resolve()
    assert engine._readonly_copy_source is None   # restored afterwards


def test_a_script_at_the_top_needs_no_cd(engine, smoke):
    root, _, seen = smoke
    top = root / "main.py"
    top.write_text("print(1)\n", encoding="utf-8")
    engine._smoke_script_runner(top)("python main.py", 10)
    assert seen["command"] == "python main.py"


def test_a_project_too_large_to_copy_copies_the_scripts_folder(engine, smoke, monkeypatch):
    root, script, seen = smoke
    monkeypatch.setattr(engine, "_readonly_copy_too_large",
                        lambda source, ignore: "more than 20000 files" if Path(source) == root.resolve() else "")
    engine._smoke_script_runner(script)("python report.py", 10)
    assert Path(seen["source"]) == script.parent.resolve() and seen["command"] == "python report.py"


def test_the_copy_source_reaches_the_read_only_copy(engine, tmp_path, monkeypatch):
    big, small = tmp_path / "big", tmp_path / "big" / "tools"
    small.mkdir(parents=True)
    (small / "x.py").write_text("print(1)\n", encoding="utf-8")
    (big / "other.txt").write_text("y", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "docker")
    monkeypatch.setattr(engine, "_shell_workspace_root", big)
    monkeypatch.setattr(engine, "_sandbox_readonly", True)
    monkeypatch.setattr(engine, "_readonly_copy_source", small)
    monkeypatch.setattr(engine, "_run_in_sandbox_backend",
                        lambda b, c, t, p, i, w: seen.setdefault("files", sorted(x.name for x in Path(w).iterdir())) and "ok")
    engine._run_sandbox_command("ls", 10)
    assert seen["files"] == ["x.py"]
