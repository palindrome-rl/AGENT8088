"""The Docker fallback: an image with Python, Node and git; the registry allowlist when it stands in for a missing
native sandbox; a proxy that refuses the computer, the local network and metadata addresses; secrets masked in build
output and a token-bearing .git/config; the host user on Linux; stale containers removed; Docker Desktop not started
unasked.

Before, the fallback ran python:3.11-slim with no network, so npm and git did not exist and pip had nowhere to go; the
allowlist proxy connected to any address an allowed name resolved to; build/ and dist/ were never searched for
secrets; files came out owned by root; and a killed session left its containers behind."""
import socket
from types import SimpleNamespace

import pytest

from agent8088 import egress_proxy


def _resolves_to(monkeypatch, *addresses):
    def fake(host, port, type=0):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
                for a in addresses]
    monkeypatch.setattr(egress_proxy.socket, "getaddrinfo", fake)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "192.168.3.69", "172.17.0.1", "169.254.169.254",
                                     "::1", "fd00::1", "::ffff:10.0.0.1", "100.64.0.1", "0.0.0.0"])
def test_the_proxy_refuses_private_and_host_addresses(monkeypatch, address):
    _resolves_to(monkeypatch, address)
    assert egress_proxy.public_address("pypi.org", 443) is None


def test_the_proxy_connects_to_the_public_address_it_checked(monkeypatch):
    _resolves_to(monkeypatch, "10.0.0.5", "151.101.0.223")
    family, sockaddr = egress_proxy.public_address("pypi.org", 443)
    assert family == socket.AF_INET and sockaddr == ("151.101.0.223", 443)


@pytest.mark.parametrize("name", ["host.docker.internal", "gateway.docker.internal", "metadata.google.internal",
                                  "localhost", "LOCALHOST."])
def test_the_proxy_refuses_host_names_whatever_they_resolve_to(monkeypatch, name):
    _resolves_to(monkeypatch, "151.101.0.223")
    assert egress_proxy.public_address(name, 443) is None


def test_a_refused_address_gets_a_403_before_any_connection(monkeypatch):
    _resolves_to(monkeypatch, "169.254.169.254")
    sent = []
    conn = SimpleNamespace(settimeout=lambda s: None, sendall=sent.append, close=lambda: None,
                           recv=lambda size, chunks=iter([b"CONNECT evil.example.com:443 HTTP/1.1\r\n\r\n", b""]):
                           next(chunks))
    egress_proxy.handle(conn, ["*.example.com"])
    assert sent and sent[0].startswith(b"HTTP/1.1 403") and b"metadata service" in sent[0]


@pytest.fixture
def docker_engine(engine, monkeypatch):
    monkeypatch.setattr(engine, "DOCKER_NETWORK", "none")
    monkeypatch.setattr(engine, "DOCKER_NETWORK_CONFIGURED", False)
    monkeypatch.setattr(engine, "SANDBOX_NETWORK_OPEN", False)
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "SANDBOX_ALLOWED_DOMAINS", [])
    monkeypatch.delitem(engine.APP_CONFIG, "sandbox_allowed_domains", raising=False)
    return engine


def test_docker_standing_in_for_native_gets_the_registry_allowlist(docker_engine):
    engine = docker_engine
    assert engine._docker_network_mode() == "allowlist"
    assert "pypi.org" in engine._docker_allowed_domains() and "registry.npmjs.org" in engine._docker_allowed_domains()


def test_docker_chosen_on_purpose_or_configured_keeps_its_network(docker_engine, monkeypatch):
    engine = docker_engine
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "docker")
    assert engine._docker_network_mode() == "none"
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", "auto")
    monkeypatch.setattr(engine, "DOCKER_NETWORK_CONFIGURED", True)
    assert engine._docker_network_mode() == "none"


def test_a_configured_allowlist_is_used_as_written(docker_engine, monkeypatch):
    engine = docker_engine
    monkeypatch.setitem(engine.APP_CONFIG, "sandbox_allowed_domains", "none")
    assert engine._docker_allowed_domains() == []
    assert engine._docker_egress_args() == ("none", [])


def test_the_sandbox_image_falls_back_when_it_cannot_be_pulled(engine, monkeypatch):
    monkeypatch.setattr(engine, "DOCKER_IMAGE_CONFIGURED", False)
    monkeypatch.setattr(engine, "DOCKER_IMAGE", engine._SANDBOX_IMAGE)
    monkeypatch.setattr(engine, "_sandbox_image_chosen", "")
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "pull refused")
    assert engine._sandbox_image() == engine._SANDBOX_IMAGE_FALLBACK
    monkeypatch.setattr(engine, "_sandbox_image_chosen", "")
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    assert engine._sandbox_image() == engine._SANDBOX_IMAGE


def test_a_configured_image_is_never_swapped(engine, monkeypatch):
    monkeypatch.setattr(engine, "DOCKER_IMAGE_CONFIGURED", True)
    monkeypatch.setattr(engine, "DOCKER_IMAGE", "my/image:1")
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: pytest.fail("no pull for a configured image"))
    assert engine._sandbox_image() == "my/image:1"


@pytest.fixture
def run(engine, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_docker_sandbox_broken", False)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    monkeypatch.setattr(engine, "_sandbox_image", lambda: "img")
    monkeypatch.setattr(engine, "_docker_egress_args", lambda: ("none", []))
    monkeypatch.setattr(engine, "_clean_stale_docker_sandbox", lambda: None)
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=25: seen.setdefault("argv", argv) and "ok")

    def go(workspace):
        seen.clear()
        engine._exec_docker_command("ls", 10, workspace=workspace)
        return seen["argv"]
    return go


def _masked(argv):
    return [part.split("dst=", 1)[1].split(",", 1)[0] for part in argv if "sandbox-empty" in part]


def test_secrets_in_build_output_and_a_token_in_git_config_are_masked(run, tmp_path):
    ws = tmp_path / "ws"
    for rel in ("build/.env", "dist/app/.env.production", "node_modules/pkg/test/key.pem", ".venv/lib/x.pem"):
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text("x", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text('[remote "origin"]\n\turl = https://me:ghp_abc@github.com/x/y\n',
                                        encoding="utf-8")
    masked = _masked(run(ws))
    assert "/workspace/build/.env" in masked and "/workspace/dist/app/.env.production" in masked
    assert "/workspace/.git/config" in masked
    assert not any("node_modules" in m or ".venv" in m for m in masked)


def test_a_plain_git_config_is_left_alone(run, tmp_path):
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    (ws / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/x/y\n', encoding="utf-8")
    assert _masked(run(ws)) == []


@pytest.mark.parametrize("platform, user", [("linux", True), ("win32", False), ("darwin", False)])
def test_linux_runs_as_the_host_user(engine, run, tmp_path, monkeypatch, platform, user):
    monkeypatch.setattr(engine.sys, "platform", platform)
    monkeypatch.setattr(engine.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(engine.os, "getgid", lambda: 1000, raising=False)
    argv = run(tmp_path / "ws")
    assert ("--user" in argv and "1000:1000" in argv and "HOME=/tmp" in argv) is user


def test_stale_containers_and_old_proxies_are_removed(engine, monkeypatch):
    import psutil
    calls = []
    current = engine._docker_egress_key(engine._docker_allowed_domains())
    listing = {("ps",): f"agent8088-999999-0123456789ab\nagent8088-{engine.os.getpid()}-0123456789ab\n"
                        f"agent8088-egress-proxy-{current}\nagent8088-egress-proxy-ffffffffff\nmy-own-container\n",
               ("network",): f"agent8088-egress-{current}\nagent8088-egress-ffffffffff\n"}

    def fake_run(argv, **kwargs):
        calls.append(argv[1:])
        out = listing.get((argv[1],), "") if argv[2] in ("-a", "ls") else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid != 999999)
    monkeypatch.setattr(engine, "_other_agent8088_running", lambda ps: False)
    monkeypatch.setattr(engine, "_docker_stale_cleaned", False)
    engine._clean_stale_docker_sandbox()
    removed = next(c for c in calls if c[:2] == ["rm", "-f"])[2:]
    assert sorted(removed) == ["agent8088-999999-0123456789ab", "agent8088-egress-proxy-ffffffffff"]
    assert ["network", "rm", "agent8088-egress-ffffffffff"] in calls
    assert not any("my-own-container" in c for call in calls for c in call if call[0] == "rm")
    calls.clear()
    engine._clean_stale_docker_sandbox()
    assert calls == []  # once per process


def test_docker_desktop_is_not_started_unasked(engine, monkeypatch):
    monkeypatch.delenv("AGENT8088_DOCKER_AUTOSTART", raising=False)
    monkeypatch.delitem(engine.APP_CONFIG, "docker_autostart", raising=False)
    monkeypatch.setattr(engine, "_docker_available", lambda: False)
    monkeypatch.setattr(engine, "_which_executable", lambda name: "docker")
    monkeypatch.setattr(engine.subprocess, "Popen", lambda *a, **k: pytest.fail("Docker Desktop was started"))
    assert engine._start_docker_desktop(sleep=lambda s: None) is False


def test_old_proxies_stay_while_another_session_runs(engine, monkeypatch):
    import psutil
    calls = []
    listing = "agent8088-999999-0123456789ab\nagent8088-egress-proxy-ffffffffff\n"

    def fake_run(argv, **kwargs):
        calls.append(argv[1:])
        out = listing if argv[1:3] == ["ps", "-a"] else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(engine, "_other_agent8088_running", lambda ps: True)
    monkeypatch.setattr(engine, "_docker_stale_cleaned", False)
    engine._clean_stale_docker_sandbox()
    assert ["rm", "-f", "agent8088-999999-0123456789ab"] in calls
    assert not any("agent8088-egress-proxy-ffffffffff" in part for call in calls for part in call)
    assert not any(call[:2] == ["network", "rm"] for call in calls)


def test_another_session_is_recognised(engine, monkeypatch):
    import psutil
    procs = [SimpleNamespace(info={"pid": 1, "cmdline": ["/usr/bin/python3", "-m", "pytest"]}),
             SimpleNamespace(info={"pid": 2, "cmdline": [r"C:\x\agent8088.exe", "--web"]})]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(procs))
    assert engine._other_agent8088_running(psutil) is True
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(procs[:1]))
    assert engine._other_agent8088_running(psutil) is False


def test_a_folder_of_keys_is_masked_as_one(run, tmp_path):
    ws = tmp_path / "ws"
    certs = ws / "tests" / "certs"
    certs.mkdir(parents=True)
    for n in range(6):
        (certs / f"key{n}.pem").write_text("x", encoding="utf-8")
    (ws / ".env").write_text("x", encoding="utf-8")
    argv = run(ws)
    mounts = [p for p in argv if "sandbox-empty" in p]
    assert any("sandbox-empty-dir" in p and p.endswith("dst=/workspace/tests/certs,readonly") for p in mounts)
    assert any(p.endswith("dst=/workspace/.env,readonly") and "sandbox-empty-dir" not in p for p in mounts)
    assert len(mounts) == 2


def test_too_many_to_mask_is_refused_not_run(engine, run, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_MASK_MAX_MOUNTS", 3)
    ws = tmp_path / "ws"
    for n in range(5):
        (ws / f"d{n}").mkdir(parents=True)
        (ws / f"d{n}" / ".env").write_text("x", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=25: seen.setdefault("argv", argv) and "ok")
    out = engine._exec_docker_command("ls", 10, workspace=ws)
    assert out.startswith("Error: more than 3 secret files") and "argv" not in seen


def test_the_fallback_and_proxy_images_are_pinned(engine):
    for image in (engine._SANDBOX_IMAGE_FALLBACK, engine._EGRESS_PROXY_IMAGE):
        assert "@sha256:" in image and engine._DOCKER_IMAGE_RE.fullmatch(image)


def test_the_managed_search_still_starts_docker_desktop(engine, monkeypatch, tmp_path):
    started = []
    monkeypatch.delenv("AGENT8088_DOCKER_AUTOSTART", raising=False)
    monkeypatch.delitem(engine.APP_CONFIG, "docker_autostart", raising=False)
    monkeypatch.setattr(engine, "_docker_available", lambda: bool(started))
    monkeypatch.setattr(engine, "_which_executable", lambda name: "docker")
    app = tmp_path / "Docker Desktop.exe"
    app.write_text("", encoding="utf-8")
    monkeypatch.setitem(engine._DOCKER_DESKTOP_APPS, engine.sys.platform, [app])
    monkeypatch.setattr(engine.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert engine._start_docker_desktop(sleep=lambda s: None) is False and not started
    assert engine._start_docker_desktop(sleep=lambda s: None, purpose="searxng") is True and started
    started.clear()
    monkeypatch.setitem(engine.APP_CONFIG, "docker_autostart", "0")
    assert engine._start_docker_desktop(sleep=lambda s: None, purpose="searxng") is False and not started



def test_a_host_allowed_this_session_reaches_docker(docker_engine, monkeypatch):
    engine = docker_engine
    monkeypatch.setattr(engine, "_session_allowed_hosts", [])
    before = engine._docker_egress_key(engine._docker_allowed_domains())
    engine.grant_escalation("sandbox_host:api.example.com", "api.example.com")
    assert "api.example.com" in engine._docker_allowed_domains()
    assert engine._docker_egress_key(engine._docker_allowed_domains()) != before


def test_docker_commands_share_the_package_caches(engine, run, tmp_path):
    argv = run(tmp_path / "ws")
    cache = str((tmp_path / "data" / "sandbox-cache").resolve())
    assert f"type=bind,src={cache},dst=/cache" in argv
    assert "PIP_CACHE_DIR=/cache/pip" in argv and "npm_config_cache=/cache/npm" in argv


def test_a_read_only_docker_run_gets_no_cache(engine, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "_docker_sandbox_broken", False)
    monkeypatch.setattr(engine, "_ensure_docker_image", lambda image: "")
    monkeypatch.setattr(engine, "_sandbox_image", lambda: "img")
    monkeypatch.setattr(engine, "_docker_egress_args", lambda: ("none", []))
    monkeypatch.setattr(engine, "_clean_stale_docker_sandbox", lambda: None)
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(engine, "_exec_process", lambda argv, timeout=25: seen.setdefault("argv", argv) and "ok")
    engine._exec_docker_command("ls", 10, workspace=tmp_path / "ws", readonly=True)
    assert not any("dst=/cache" in part for part in seen["argv"])
