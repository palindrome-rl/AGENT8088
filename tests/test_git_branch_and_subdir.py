"""Branches must be creatable, and a repo below the shell cwd must be reachable.

Three defects, all observed in one live run against a writeable workspace. The
agent was asked to put a folder under version control, commit it, branch, and
commit again. It managed the first commit and nothing after it:

1. There were no `git_branch` / `git_checkout` tools, so the model fell back to
   `execute_shell`, where `git checkout -b` is refused by the command policy and
   `git switch -c` dies on `unable to create directory for
   .git/refs/heads/feature/first` -- the sandbox denies writes under .git.

2. Every git tool ran in SHELL_CWD with no argument able to point elsewhere, so
   a repository one directory down reported "not a git repository". A `repo`
   argument was accepted and silently dropped.

3. `resolve_write_path` diverted an invented relative path into artifacts/ even
   when it named a file inside a real repository, so the file and the repo ended
   up in different directories and the commit had nothing to stage. Nothing
   could repair it afterwards, because .git is unwritable from the shell.
"""
import subprocess

import pytest


# --- 1. branch and checkout tools exist, on the host --------------------

@pytest.mark.parametrize("tool", ["git_branch", "git_checkout"])
def test_branch_tools_exist_and_run_on_host(engine, tool):
    spec = engine.TOOL_SPECS.get(tool)
    assert spec is not None, f"{tool} is not registered"
    assert spec.get("host"), f"{tool} must run on the host: the sandbox denies .git writes"
    assert spec.get("mode") == "shell"


def test_git_branch_argv(engine):
    argv = engine._structured_tool_argv("git_branch", {"name": "feature/first"})
    assert argv == ["git", "branch", "--", "feature/first"]


def test_git_branch_requires_a_name(engine):
    with pytest.raises(ValueError):
        engine._structured_tool_argv("git_branch", {})


def test_git_checkout_argv(engine):
    argv = engine._structured_tool_argv("git_checkout", {"name": "main"})
    assert argv == ["git", "checkout", "--", "main"]


def test_git_checkout_creates_and_switches_in_one_step(engine):
    argv = engine._structured_tool_argv(
        "git_checkout", {"name": "feature/first", "create": "true"})
    assert argv == ["git", "checkout", "-b", "feature/first"]


def test_git_checkout_create_false_does_not_create(engine):
    """The model passes booleans as strings; "false" must not read as true."""
    argv = engine._structured_tool_argv(
        "git_checkout", {"name": "main", "create": "false"})
    assert argv == ["git", "checkout", "--", "main"]


@pytest.mark.parametrize("tool", ["git_branch", "git_checkout"])
@pytest.mark.parametrize("bad", ["-b", "--force", "a\nb", "a\0b"])
def test_branch_tools_reject_flags_and_control_characters(engine, tool, bad):
    with pytest.raises(ValueError):
        engine._structured_tool_argv(tool, {"name": bad})


def test_git_branch_really_creates_a_branch(engine, tmp_path, monkeypatch):
    """The sandbox cannot do this at all; the host path must."""
    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    engine._exec_structured_tool("git_init", {}, timeout=30)
    (tmp_path / "f.txt").write_text("x\n")
    engine._exec_structured_tool("git_commit", {"message": "initial"}, timeout=30)
    engine._exec_structured_tool("git_branch", {"name": "feature/first"}, timeout=30)

    branches = subprocess.run(["git", "-C", str(tmp_path), "branch"],
                              capture_output=True, text=True).stdout
    assert "feature/first" in branches


# --- 2. a repo below the shell cwd is reachable -------------------------

@pytest.mark.parametrize("tool,args,tail", [
    ("git_commit", {"message": "m"}, ["commit", "-m", "m"]),
    ("git_branch", {"name": "b"}, ["branch", "--", "b"]),
    ("git_checkout", {"name": "b"}, ["checkout", "--", "b"]),
    ("git_push", {"branch": "main"}, ["push", "origin", "main"]),
    ("git_remote", {"action": "add", "name": "origin", "url": "https://x/y.git"},
     ["remote", "add", "origin", "https://x/y.git"]),
    ("git_status", {}, ["status", "--short", "--branch"]),
    ("git_log", {}, ["log", "--oneline", "-20"]),
    ("git_diff", {}, ["diff"]),
])
def test_git_tools_honour_a_repo_directory(engine, tool, args, tail):
    argv = engine._structured_tool_argv(tool, dict(args, repo="sub"))
    assert argv == ["git", "-C", "sub"] + tail


@pytest.mark.parametrize("tool,args,tail", [
    ("git_commit", {"message": "m"}, ["commit", "-m", "m"]),
    ("git_push", {"branch": "main"}, ["push", "origin", "main"]),
    ("git_status", {}, ["status", "--short", "--branch"]),
])
def test_omitting_repo_leaves_the_command_unchanged(engine, tool, args, tail):
    assert engine._structured_tool_argv(tool, args) == ["git"] + tail


@pytest.mark.parametrize("bad", ["-C", "--git-dir=/etc", "a\nb", "a\0b"])
def test_repo_directory_rejects_flags_and_control_characters(engine, bad):
    with pytest.raises(ValueError):
        engine._structured_tool_argv("git_commit", {"message": "m", "repo": bad})


def test_git_push_approval_prompt_ignores_the_repo_directory(engine):
    """`repo` says where the repo is, not where the push goes."""
    target = engine._git_push_target({"branch": "main", "repo": "sub"})
    assert target == "origin main"


def test_commit_stages_inside_the_named_repo(engine, tmp_path, monkeypatch):
    """`git add -A` runs as its own process, so it needs the prefix too.

    Without it the stage ran in SHELL_CWD while the commit ran in the repo, and
    the commit failed with "nothing to commit" on a dirty tree.
    """
    monkeypatch.setattr(engine, "SHELL_CWD", tmp_path)
    engine._exec_structured_tool("git_init", {"directory": "sub"}, timeout=30)
    (tmp_path / "sub" / "f.txt").write_text("x\n")
    engine._exec_structured_tool(
        "git_commit", {"message": "initial", "repo": "sub"}, timeout=30)

    log = subprocess.run(["git", "-C", str(tmp_path / "sub"), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "initial" in log


# --- 3. a write into a repository is not an invented location -----------

def test_write_into_an_existing_repo_is_not_diverted(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    assert engine.resolve_write_path("repo/sample.txt") == tmp_path / "repo" / "sample.txt"


def test_a_nested_path_inside_a_repo_is_not_diverted(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    assert engine.resolve_write_path("repo/src/main.py") == \
        tmp_path / "repo" / "src" / "main.py"


def test_write_outside_a_repo_still_goes_to_artifacts(engine, tmp_path, monkeypatch):
    """The diversion is deliberate everywhere else; only repositories are exempt."""
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    (tmp_path / "plain").mkdir()

    assert engine.resolve_write_path("plain/sample.txt") == \
        tmp_path / "artifacts" / "plain" / "sample.txt"


def test_a_bare_filename_still_goes_to_artifacts(engine, tmp_path, monkeypatch):
    """Even when the project root is itself a repository."""
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    (tmp_path / ".git").mkdir()

    assert engine.resolve_write_path("sample.txt") == \
        tmp_path / "artifacts" / "sample.txt"
