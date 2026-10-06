"""Exercise real local Git checkouts; no GitHub account/network required."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRANCH = "AGENT8088-v1.2"
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("git") or not shutil.which("bash"),
    reason="real POSIX checkout tests",
)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "Source With Spaces"
    source.mkdir()
    git("init", "-b", BRANCH, cwd=source)
    git("config", "user.name", "Installer Test", cwd=source)
    git("config", "user.email", "installer-test@example.invalid", cwd=source)
    (source / "app.txt").write_text("initial\n", encoding="utf-8")
    git("add", ".", cwd=source)
    git("commit", "-m", "initial fixture", cwd=source)
    return source


def checkout(tmp_path, source, override=""):
    home = tmp_path / "Agent Home"
    home.mkdir(exist_ok=True)
    target = home / "agent8088"
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    function = re.search(r"(?ms)^clone_repo\(\) \{.*?^\}", text).group()
    script = (
        "set -e\n"
        "log_info() { echo INFO:$*; }\n"
        "log_warn() { echo WARN:$*; }\n"
        "log_success() { echo OK:$*; }\n"
        "log_error() { echo ERR:$*; }\n"
        "probe_repo_access() { :; }\n"
        "run_logged() { shift; \"$@\"; }\n"
        "show_step_failure() { :; }\n"
        f"AGENT8088_HOME={shlex.quote(str(home))}\n"
        f"INSTALL_DIR={shlex.quote(str(target))}\n"
        f"REPO_URL={shlex.quote(str(source))}\n"
        f"BRANCH={BRANCH}\nT_GIT=60\n"
        + override + "\n" + function + "\nclone_repo\n"
    )
    env = {**os.environ, "HOME": str(tmp_path),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    return result, target


def test_fresh_clone_and_repeat_update_work_with_spaces(tmp_path, source):
    result, target = checkout(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "app.txt").read_text() == "initial\n"
    (source / "app.txt").write_text("updated\n", encoding="utf-8")
    git("add", ".", cwd=source)
    git("commit", "-m", "updated fixture", cwd=source)
    result, target = checkout(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git("rev-parse", "HEAD", cwd=target) == git("rev-parse", "HEAD", cwd=source)
    assert (target / "app.txt").read_text() == "updated\n"


def test_incomplete_non_git_install_is_preserved(tmp_path, source):
    home = tmp_path / "Agent Home"
    target = home / "agent8088"
    target.mkdir(parents=True)
    (target / "user-note.txt").write_text("keep me\n", encoding="utf-8")
    result, target = checkout(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = list(home.glob("agent8088.saved.*/checkout/user-note.txt"))
    assert len(saved) == 1 and saved[0].read_text() == "keep me\n"
    assert (target / ".git").is_dir()


def test_interrupted_clone_with_no_commit_is_preserved(tmp_path, source):
    target = tmp_path / "Agent Home" / "agent8088"
    target.mkdir(parents=True)
    git("init", cwd=target)
    (target / "user-note.txt").write_text("keep me\n", encoding="utf-8")
    result, target = checkout(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(target.parent.glob("agent8088.broken-*/user-note.txt"))


def test_update_stashes_staged_unstaged_and_untracked_changes(tmp_path, source):
    result, target = checkout(tmp_path, source)
    assert result.returncode == 0
    git("config", "user.name", "Installer Test", cwd=target)
    git("config", "user.email", "installer-test@example.invalid", cwd=target)
    (target / "app.txt").write_text("staged change\n", encoding="utf-8")
    git("add", "app.txt", cwd=target)
    (target / "app.txt").write_text("unstaged change\n", encoding="utf-8")
    (target / "user-note.txt").write_text("untracked change\n", encoding="utf-8")
    result, target = checkout(tmp_path, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "agent8088-install-autostash" in git("stash", "list", cwd=target)
    assert "unstaged change" in git("show", "stash:app.txt", cwd=target)
    assert "staged change" in git("show", "stash^2:app.txt", cwd=target)
    assert "untracked change" in git("show", "stash^3:user-note.txt", cwd=target)


@pytest.mark.parametrize("failed_command", ["stash", "remote", "fetch", "checkout"])
def test_update_failure_never_claims_success(tmp_path, source, failed_command):
    result, target = checkout(tmp_path, source)
    assert result.returncode == 0
    (target / "app.txt").write_text("local change\n", encoding="utf-8")
    git("config", "user.name", "Installer Test", cwd=target)
    git("config", "user.email", "installer-test@example.invalid", cwd=target)
    original = git("rev-parse", "HEAD", cwd=target)
    real_git = shlex.quote(shutil.which("git"))
    override = (
        f'git() {{ if [ "$1" = "{failed_command}" ]; then '
        f'echo "fixture failure: {failed_command}" >&2; return 1; fi; '
        f'{real_git} "$@"; }}'
    )
    result, target = checkout(tmp_path, source, override)
    assert result.returncode != 0
    assert "Repository ready" not in result.stdout
    assert git("rev-parse", "HEAD", cwd=target) == original
    if failed_command == "stash":
        assert (target / "app.txt").read_text() == "local change\n"
