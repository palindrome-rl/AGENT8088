"""`agent8088 --update` stays on the branch the install was made from.

development, staging-1.3 and AGENT8088-v1.3 carry the same tree, so UPDATE_BRANCH
(the production branch) is the same on all of them. An install made with
AGENT8088_BRANCH=development or staging-1.3 must not be moved to production by
its first update: the branch checked out in the install is the channel the user
chose, and it is followed as long as the remote still has it.
"""
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def cli(engine):
    from agent8088 import cli as module
    return importlib.reload(module)


def _fake_git(checked_out, remote_heads, default="AGENT8088-v1.3"):
    calls = []

    def git(_install_dir, *args):
        calls.append(args)
        if args[:2] == ("symbolic-ref", "--short"):
            return SimpleNamespace(returncode=0 if checked_out else 1,
                                   stdout=(checked_out or "") + "\n", stderr="")
        if args[:2] == ("ls-remote", "--heads"):
            name = args[-1]
            found = name in remote_heads
            return SimpleNamespace(returncode=0, stdout=f"abc\trefs/heads/{name}\n" if found else "",
                                   stderr="")
        if args[:2] == ("ls-remote", "--symref"):
            return SimpleNamespace(returncode=0, stdout=f"ref: refs/heads/{default}\tHEAD\n", stderr="")
        raise AssertionError(f"unexpected git call {args}")
    return git, calls


@pytest.mark.parametrize("branch", ["development", "staging-1.3", "AGENT8088-v1.3"])
def test_an_install_updates_on_the_branch_it_is_on(cli, monkeypatch, tmp_path, branch):
    git, _calls = _fake_git(branch, {"development", "staging-1.3", "AGENT8088-v1.3"})
    monkeypatch.setattr(cli, "_git", git)
    assert cli._resolve_update_branch(tmp_path) == (branch, "")


def test_a_detached_install_updates_on_the_release_branch(cli, monkeypatch, tmp_path):
    git, _calls = _fake_git(None, {"AGENT8088-v1.3"})
    monkeypatch.setattr(cli, "_git", git)
    assert cli._resolve_update_branch(tmp_path) == (cli.UPDATE_BRANCH, "")


def test_a_retired_branch_falls_back_to_the_release_branch_and_says_so(cli, monkeypatch, tmp_path):
    git, _calls = _fake_git("staging-1.2", {"AGENT8088-v1.3"})
    monkeypatch.setattr(cli, "_git", git)
    branch, note = cli._resolve_update_branch(tmp_path)
    assert branch == cli.UPDATE_BRANCH
    assert "staging-1.2" in note and cli.UPDATE_BRANCH in note


def test_the_release_branch_gone_too_falls_back_to_the_remote_default(cli, monkeypatch, tmp_path):
    git, _calls = _fake_git(None, set(), default="main")
    monkeypatch.setattr(cli, "_git", git)
    branch, note = cli._resolve_update_branch(tmp_path)
    assert branch == "main"
    assert "main" in note
