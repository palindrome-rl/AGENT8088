from types import SimpleNamespace

from agent8088 import cli


def _result(code=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_update_refuses_dirty_install_without_stashing(tmp_path, monkeypatch):
    install = tmp_path / "agent8088"
    install.mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return _result(stdout=" M local.py\n")

    monkeypatch.setattr("subprocess.run", run)
    assert cli._run_update() is False
    assert calls == [["git", "status", "--porcelain"]]


def _fake_git(calls, *, dirty="", branch_on_remote=True, remote_head="main",
              head_commits=("aaaaaaa", "bbbbbbb")):
    """A stand-in for subprocess.run covering the git calls _run_update makes.

    Nothing here executes git: the point of these tests is the argv and the
    control flow, and running the real thing would need a real install dir."""
    heads = iter(head_commits)

    def run(command, **kwargs):
        calls.append(command)
        head = command[:3]
        if head == ["git", "status", "--porcelain"]:
            return _result(stdout=dirty)
        if head == ["git", "ls-remote", "--heads"]:
            return _result(stdout="c0ffee\trefs/heads/x\n" if branch_on_remote else "")
        if head == ["git", "ls-remote", "--symref"]:
            return _result(stdout=f"ref: refs/heads/{remote_head}\tHEAD\n"
                           if remote_head else "")
        if head == ["git", "rev-parse", "--short"]:
            return _result(stdout=next(heads, "bbbbbbb") + "\n")
        return _result()

    return run


def test_update_fetches_the_named_branch_and_reinstalls(tmp_path, monkeypatch, capsys):
    """`git pull` moves whatever branch is checked out against whatever upstream
    it has. The branch to update to is named explicitly instead."""
    (tmp_path / "agent8088").mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr("subprocess.run", _fake_git(calls))

    assert cli._run_update() is True

    assert ["git", "fetch", "--depth", "1", "origin", cli.UPDATE_BRANCH] in calls
    assert ["git", "checkout", "-B", cli.UPDATE_BRANCH, "FETCH_HEAD"] in calls
    assert not any("pull" in command for command in calls), "pull is what this replaced"
    assert any("pip" in command and "--reinstall-package" in command for command in calls)
    assert "aaaaaaa -> bbbbbbb" in capsys.readouterr().out


def test_update_reports_when_already_at_the_tip(tmp_path, monkeypatch, capsys):
    (tmp_path / "agent8088").mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    monkeypatch.setattr("subprocess.run",
                        _fake_git([], head_commits=("aaaaaaa", "aaaaaaa")))

    assert cli._run_update() is True
    assert "Already at the latest commit" in capsys.readouterr().out


def test_windows_update_defers_reinstall_until_launcher_exits(
        tmp_path, monkeypatch, capsys):
    install = tmp_path / "agent8088"
    launcher = install / "venv" / "Scripts" / "agent8088.exe"
    launcher.parent.mkdir(parents=True)
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli.sys, "argv", [str(launcher), "--update"])
    monkeypatch.setattr("subprocess.run", _fake_git([], head_commits=("a", "b")))
    popen_calls = []
    monkeypatch.setattr("subprocess.Popen",
                        lambda command, **kwargs: popen_calls.append((command, kwargs)))

    assert cli._run_update() is True

    command, kwargs = popen_calls[0]
    assert command[:3] == ["cmd", "/d", "/c"]
    assert "--reinstall-package agent8088" in command[3]
    assert "timeout /t 2" in command[3]
    assert "for /L %i in (1,1,30)" in command[3]
    assert kwargs["creationflags"] == 0x00000008
    output = capsys.readouterr().out
    assert "after this process exits" in output
    assert "update.log" in output


def test_update_names_the_files_in_the_way_and_offers_force(tmp_path, monkeypatch, capsys):
    """The old message said only that there were local changes, and pointed at a
    /update command that does not exist."""
    (tmp_path / "agent8088").mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr("subprocess.run",
                        _fake_git(calls, dirty=" M src/agent8088/cli.py\n?? scratch.py\n"))

    assert cli._run_update() is False

    out = capsys.readouterr().out
    assert "src/agent8088/cli.py" in out and "scratch.py" in out
    assert "--force" in out
    assert calls == [["git", "status", "--porcelain"]], "must not touch the tree"


def test_update_force_discards_local_changes(tmp_path, monkeypatch):
    (tmp_path / "agent8088").mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr("subprocess.run",
                        _fake_git(calls, dirty=" M src/agent8088/cli.py\n"))

    assert cli._run_update(force=True) is True

    assert ["git", "reset", "--hard"] in calls
    assert ["git", "clean", "-fd"] in calls
    assert ["git", "checkout", "-B", cli.UPDATE_BRANCH, "FETCH_HEAD"] in calls


def test_update_falls_back_when_the_branch_was_renamed(tmp_path, monkeypatch, capsys):
    """A retired or renamed release branch must still update, loudly, rather
    than fail on git's raw 'couldn't find remote ref'."""
    (tmp_path / "agent8088").mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr("subprocess.run",
                        _fake_git(calls, branch_on_remote=False, remote_head="release"))

    assert cli._run_update() is True

    out = capsys.readouterr().out
    assert cli.UPDATE_BRANCH in out and "release" in out
    assert ["git", "checkout", "-B", "release", "FETCH_HEAD"] in calls


def test_update_stops_when_no_branch_can_be_resolved(tmp_path, monkeypatch, capsys):
    (tmp_path / "agent8088").mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr("subprocess.run",
                        _fake_git(calls, branch_on_remote=False, remote_head=""))

    assert cli._run_update() is False

    assert "Nothing was changed." in capsys.readouterr().out
    assert not any("fetch" in command or "checkout" in command for command in calls)
