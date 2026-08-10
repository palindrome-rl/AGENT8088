"""Tests for SearXNG container provisioning.

Never runs docker, never reaches the network, and never writes outside
tmp_path — the repo convention is that tests and verify scripts do not touch
real user state.
"""
from types import SimpleNamespace

from agent8088 import searxng_provision as sp
from tests.conftest import assert_owner_only


def _ok(stdout="true"):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail(stderr="no such container"):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


# ---------------------------------------------------------------------------
# settings.yml generation
# ---------------------------------------------------------------------------
def test_settings_enable_json_and_disable_limiter(tmp_path):
    path = sp.write_settings(tmp_path)
    text = path.read_text()
    assert "json" in text and "limiter: false" in text


def test_settings_secret_key_is_random_and_not_the_upstream_placeholder(tmp_path):
    a = sp.write_settings(tmp_path / "a").read_text()
    b = sp.write_settings(tmp_path / "b").read_text()
    assert "ultrasecretkey" not in a
    assert a != b


def test_settings_file_is_owner_only(tmp_path):
    assert_owner_only(sp.write_settings(tmp_path))


def test_write_settings_is_idempotent_and_preserves_secret(tmp_path):
    first = sp.write_settings(tmp_path).read_text()
    second = sp.write_settings(tmp_path).read_text()
    assert first == second


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------
def test_start_binds_loopback_only(tmp_path, monkeypatch):
    """The JSON API is unauthenticated — publishing it on 0.0.0.0 would put an
    open search proxy on the LAN."""
    seen = {}
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "status", lambda: {"running": False, "detail": "container does not exist"})
    monkeypatch.setattr(sp, "_run",
                        lambda argv, **kw: seen.setdefault("argv", argv) and _ok() or _ok())
    sp.start(tmp_path)
    argv = seen["argv"]
    publish = argv[argv.index("-p") + 1]
    assert publish == "127.0.0.1:8888:8080"
    assert "0.0.0.0" not in " ".join(argv)


def test_start_mounts_the_generated_settings_dir(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "status", lambda: {"running": False, "detail": "container does not exist"})
    monkeypatch.setattr(sp, "_run",
                        lambda argv, **kw: seen.setdefault("argv", argv) and _ok() or _ok())
    sp.start(tmp_path)
    mount = seen["argv"][seen["argv"].index("-v") + 1]
    assert mount.endswith(":/etc/searxng")
    assert (tmp_path / "searxng" / "settings.yml").exists()


def test_start_reports_missing_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda n: None)
    result = sp.start(tmp_path)
    assert result["ok"] is False and "docker" in result["detail"].lower()


def test_start_is_a_noop_when_already_running(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "status", lambda: {"running": True, "detail": "running"})
    called = []
    monkeypatch.setattr(sp, "_run", lambda argv, **kw: called.append(argv) or _ok())
    result = sp.start(tmp_path)
    assert result["ok"] is True and "already running" in result["detail"]
    assert called == []  # no docker run


def test_start_removes_a_stopped_leftover_before_running(tmp_path, monkeypatch):
    """A name collision with a stopped container would make `docker run` fail."""
    argvs = []
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "status",
                        lambda: {"running": False,
                                 "detail": "container exists but is stopped"})
    monkeypatch.setattr(sp, "_run", lambda argv, **kw: argvs.append(argv) or _ok())
    sp.start(tmp_path)
    assert any("rm" in a for a in argvs) and any("run" in a for a in argvs)


def test_start_surfaces_docker_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "status", lambda: {"running": False, "detail": "container does not exist"})
    monkeypatch.setattr(sp, "_run",
                        lambda argv, **kw: _fail("port is already allocated"))
    result = sp.start(tmp_path)
    assert result["ok"] is False and "already allocated" in result["detail"]


def test_status_reports_not_running_when_inspect_fails(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "_run", lambda argv, **kw: _fail())
    assert sp.status()["running"] is False


def test_status_reports_running(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "_run", lambda argv, **kw: _ok("true\n"))
    assert sp.status()["running"] is True


def test_status_without_docker_does_not_shell_out(monkeypatch):
    called = []
    monkeypatch.setattr(sp.shutil, "which", lambda n: None)
    monkeypatch.setattr(sp, "_run", lambda argv, **kw: called.append(argv) or _ok())
    assert sp.status()["running"] is False and called == []


def test_stop_uses_container_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(sp.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sp, "_run",
                        lambda argv, **kw: seen.setdefault("argv", argv) and _ok() or _ok())
    sp.stop()
    assert sp.CONTAINER_NAME in seen["argv"]


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
def test_wait_ready_succeeds_once_json_answers(monkeypatch):
    calls = {"n": 0}

    def _probe(url, timeout):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("connection refused")
        return {"results": []}

    monkeypatch.setattr(sp, "_probe_json", _probe)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    assert sp.wait_ready(attempts=5)["ok"] is True


def test_wait_ready_reports_json_disabled(monkeypatch):
    def _probe(url, timeout):
        raise ValueError("not json")

    monkeypatch.setattr(sp, "_probe_json", _probe)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    result = sp.wait_ready(attempts=2)
    assert result["ok"] is False and "formats" in result["detail"]


def test_wait_ready_gives_up_and_says_so(monkeypatch):
    def _probe(url, timeout):
        raise OSError("refused")

    monkeypatch.setattr(sp, "_probe_json", _probe)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    result = sp.wait_ready(attempts=2)
    assert result["ok"] is False and "did not become ready" in result["detail"]


def test_wait_ready_never_sleeps_more_than_attempts(monkeypatch):
    sleeps = []

    def _probe(url, timeout):
        raise OSError("refused")

    monkeypatch.setattr(sp, "_probe_json", _probe)
    monkeypatch.setattr(sp.time, "sleep", lambda s: sleeps.append(s))
    sp.wait_ready(attempts=3)
    assert len(sleeps) == 3


def test_base_url_matches_the_published_port():
    assert f":{sp.HOST_PORT}/" in sp.BASE_URL and sp.BASE_URL.endswith("?q=")
