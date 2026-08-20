from agent8088 import engine, searxng_provision


def test_docker_uses_the_platform_safe_executable_resolver(monkeypatch):
    calls = []

    def resolve(name):
        calls.append(name)
        return r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"

    monkeypatch.setattr(engine, "_which_executable", resolve)

    assert searxng_provision._docker().endswith("docker.exe")
    assert calls == ["docker"]


def test_start_restarts_a_running_container_to_load_current_settings(
        monkeypatch, tmp_path):
    calls = []

    class Result:
        returncode = 0
        stdout = "agent8088-searxng\n"
        stderr = ""

    monkeypatch.setattr(searxng_provision, "_docker", lambda: "docker.exe")
    monkeypatch.setattr(
        searxng_provision, "write_settings",
        lambda home: tmp_path / "searxng" / "settings.yml")
    monkeypatch.setattr(
        searxng_provision, "status",
        lambda: {"running": True, "detail": "running"})

    def run(argv, timeout=90):
        calls.append((argv, timeout))
        return Result()

    monkeypatch.setattr(searxng_provision, "_run", run)

    result = searxng_provision.start(tmp_path)

    assert result == {
        "ok": True,
        "detail": "restarted with current settings",
        "base_url": "http://127.0.0.1:8888/search?q=",
    }
    assert calls == [
        (["docker.exe", "restart", "agent8088-searxng"], 60),
    ]


def test_start_reports_a_running_container_restart_failure(monkeypatch, tmp_path):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "Docker Desktop is not running"

    monkeypatch.setattr(searxng_provision, "_docker", lambda: "docker.exe")
    monkeypatch.setattr(
        searxng_provision, "write_settings",
        lambda home: tmp_path / "searxng" / "settings.yml")
    monkeypatch.setattr(
        searxng_provision, "status",
        lambda: {"running": True, "detail": "running"})
    monkeypatch.setattr(searxng_provision, "_run", lambda argv, timeout=90: Result())

    result = searxng_provision.start(tmp_path)

    assert result == {
        "ok": False,
        "detail": "Docker Desktop is not running",
    }
