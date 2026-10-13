"""--update must rebuild the Web UI, and say so when it could not."""
from types import SimpleNamespace

from agent8088 import cli


def _install(tmp_path, lock=True):
    web = tmp_path / "install" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text("{}", encoding="utf-8")
    if lock:
        (web / "package-lock.json").write_text("{}", encoding="utf-8")
    return tmp_path / "install", tmp_path / "home"


def test_installs_with_the_lockfile_then_builds(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    monkeypatch.setattr(cli, "_find_npm", lambda h: "/x/npm")
    seen = []
    ok, msg = cli._rebuild_web_ui(install, home, runner=lambda argv, cwd, env: seen.append(argv[1:])
                                  or SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert ok and seen == [["ci"], ["run", "build"]] and "rebuilt" in msg


def test_failed_build_is_reported_as_stale(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    monkeypatch.setattr(cli, "_find_npm", lambda h: "/x/npm")

    def runner(argv, cwd, env):
        return SimpleNamespace(returncode=1 if argv[1:] == ["run", "build"] else 0,
                               stdout="", stderr="vite: boom")
    ok, msg = cli._rebuild_web_ui(install, home, runner=runner)
    assert not ok and "NOT updated" in msg and "boom" in msg


def test_missing_node_is_a_clear_failure(tmp_path, monkeypatch):
    install, home = _install(tmp_path)
    monkeypatch.setattr(cli, "_find_npm", lambda h: None)
    ok, msg = cli._rebuild_web_ui(install, home)
    assert not ok and "Node.js" in msg


def test_install_without_web_source_is_not_an_error(tmp_path):
    ok, _ = cli._rebuild_web_ui(tmp_path, tmp_path)
    assert ok
