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


def test_update_fast_forwards_and_reinstalls(tmp_path, monkeypatch):
    install = tmp_path / "agent8088"
    install.mkdir()
    monkeypatch.setattr(cli, "_agent8088_home", lambda: tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["git", "status", "--porcelain"]:
            return _result()
        if command[:3] == ["git", "pull", "--ff-only"]:
            return _result(stdout="Already up to date.\n")
        return _result()

    monkeypatch.setattr("subprocess.run", run)
    assert cli._run_update() is True
    assert ["git", "pull", "--ff-only"] in calls
    assert not any("reset" in command for command in calls)
    assert any("pip" in command and "--reinstall-package" in command for command in calls)
