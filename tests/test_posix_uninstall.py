"""_run_uninstall() on Linux/macOS must not crash on a file it can't remove.

Reported failure: `agent8088 --uninstall` on Ubuntu died with

    Could not remove /home/usama/.agent8088: [Errno 1] Operation not
    permitted: '/home/usama/.agent8088/searxng/settings.yml'

settings.yml lives in a directory the searxng Docker container bind-mounts
and writes into, so it can end up owned by a uid this process isn't. The
`_clear_readonly` onerror callback shutil.rmtree used had no exception
handling, so a chmod that also failed (not the owner - can't chmod either)
raised out of the callback uncaught, aborting the whole rmtree walk instead
of just leaving that one file behind and clearing everything else.
"""
import os
import shutil
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from agent8088 import cli

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX uninstall path only")


def _deny_by_basename(name):
    """Make os.unlink/os.chmod fail with EPERM for files with this basename,
    the same errno the real failure reported - without needing root or a
    second uid to actually reproduce foreign ownership."""
    real_unlink, real_chmod = os.unlink, os.chmod

    def fake_unlink(path, *a, **k):
        if os.path.basename(str(path)) == name:
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_unlink(path, *a, **k)

    def fake_chmod(path, *a, **k):
        if os.path.basename(str(path)) == name:
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_chmod(path, *a, **k)

    return fake_unlink, fake_chmod


def _make_tree(home: Path):
    (home / "searxng").mkdir(parents=True)
    settings = home / "searxng" / "settings.yml"
    settings.write_text("secret_key: x", encoding="utf-8")
    (home / "config.txt").write_text("x", encoding="utf-8")
    (home / "other_dir").mkdir()
    (home / "other_dir" / "file.txt").write_text("y", encoding="utf-8")
    return settings


def test_uninstall_removes_everything_it_can_when_one_file_is_undeletable(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    settings = _make_tree(home)
    fake_unlink, fake_chmod = _deny_by_basename("settings.yml")

    monkeypatch.setattr(os, "unlink", fake_unlink)
    monkeypatch.setattr(os, "chmod", fake_chmod)
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_remove_agent8088_shim", lambda _home: False)
    monkeypatch.setattr(cli, "_remove_agent8088_config_exports", lambda: 0)

    result = cli._run_uninstall()

    assert result is False
    assert settings.exists()
    assert not (home / "config.txt").exists()
    assert not (home / "other_dir").exists()


def test_uninstall_leaves_the_surviving_directory_traversable(tmp_path, monkeypatch):
    """The onerror recovery chmods a path before retrying the delete. It must
    OR in write+execute rather than clobber the mode outright - otherwise a
    directory that merely has one unremovable child (not a permissions
    problem on the directory itself) is left without its execute bit and
    becomes unlistable, even to its own owner."""
    home = tmp_path / "agent8088"
    settings = _make_tree(home)
    fake_unlink, fake_chmod = _deny_by_basename("settings.yml")

    monkeypatch.setattr(os, "unlink", fake_unlink)
    monkeypatch.setattr(os, "chmod", fake_chmod)
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_remove_agent8088_shim", lambda _home: False)
    monkeypatch.setattr(cli, "_remove_agent8088_config_exports", lambda: 0)

    cli._run_uninstall()

    searxng_dir = home / "searxng"
    mode = searxng_dir.stat().st_mode
    assert mode & stat.S_IXUSR, "directory lost its execute bit and is now unlistable"
    assert os.listdir(searxng_dir) == ["settings.yml"]


def test_uninstall_does_not_crash_when_nothing_can_be_removed(tmp_path, monkeypatch):
    home = tmp_path / "agent8088"
    _make_tree(home)

    def deny_everything(path, *a, **k):
        raise PermissionError(1, "Operation not permitted", str(path))

    monkeypatch.setattr(os, "unlink", deny_everything)
    monkeypatch.setattr(os, "chmod", deny_everything)
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")
    monkeypatch.setattr(cli, "_agent8088_home", lambda: home)
    monkeypatch.setattr(cli, "_remove_agent8088_shim", lambda _home: False)
    monkeypatch.setattr(cli, "_remove_agent8088_config_exports", lambda: 0)

    # Must not raise.
    result = cli._run_uninstall()
    assert result is False
    assert home.exists()
