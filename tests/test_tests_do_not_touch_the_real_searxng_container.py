"""A test run must never reach the developer's real SearXNG container (see conftest._no_real_searxng_container)."""
import subprocess

from agent8088 import cli, searxng_provision


def _no_docker_for_real(monkeypatch):
    """Record any docker command and refuse it; every other command runs as it would."""
    calls = []
    real_run = subprocess.run

    def guarded(argv, *a, **k):
        if argv and "docker" in str(argv[0]).lower():
            calls.append(argv)
            raise AssertionError(f"a test ran a real docker command: {argv}")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", guarded)
    return calls


def test_stop_and_status_do_not_run_docker_for_real(monkeypatch):
    calls = _no_docker_for_real(monkeypatch)
    monkeypatch.setattr(searxng_provision, "_docker", lambda: "docker")
    assert searxng_provision.stop()["ok"] is False
    assert searxng_provision.status()["detail"] == "container does not exist"
    assert calls == []


def test_the_windows_uninstall_does_not_remove_the_real_container(tmp_path, monkeypatch):
    calls = _no_docker_for_real(monkeypatch)
    monkeypatch.setattr(searxng_provision, "_docker", lambda: "docker")
    home = tmp_path / "agent8088"
    home.mkdir()
    monkeypatch.setattr(cli, "_agent8088_link_dir", lambda: home.with_name("launcher"))
    monkeypatch.setattr(cli, "_remove_windows_user_environment", lambda *paths: True)
    monkeypatch.setattr(cli, "_purge_install_tree", lambda target: [])
    monkeypatch.setattr(cli, "_start_windows_cleanup_helper", lambda target, pid: tmp_path / "cleanup.log")
    assert cli._run_windows_uninstall(home)
    assert calls == []
