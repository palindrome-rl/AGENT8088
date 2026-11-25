"""Attack tests: real commands that try to get out of the sandbox, on every backend this machine has.

Each test runs a command through the real backend (srt on macOS and Linux, the Windows ACL runner, Docker) and checks
the result on the host, not the command's own report. They are opt-in, because they need the backend installed and
take minutes: set AGENT8088_ATTACK_TESTS=1, and pick backends with AGENT8088_ATTACK_BACKENDS=native,docker (default
both). CI runs them in the "Sandbox attack tests" job; run them locally on Windows before a release, and after every
bump of the pinned sandbox runtime or Docker image.

A backend that does not enforce a boundary at all is skipped for that test with the reason (the Windows runner limits
writes only, so it neither hides files from reads nor filters the network): the skip is the documented gap, not a
pass. A protection that a later change adds is tested only once the engine has it, so this suite runs on any build.
"""
import os
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.sandbox_attack,
    pytest.mark.skipif(os.environ.get("AGENT8088_ATTACK_TESTS") != "1",
                       reason="attack tests are opt-in: set AGENT8088_ATTACK_TESTS=1"),
]

MARK = "ATTACK-MARKER-7f3c"
BACKENDS = [b.strip() for b in os.environ.get("AGENT8088_ATTACK_BACKENDS", "native,docker").split(",") if b.strip()]
PROBE = r'''
import os, socket, sys, urllib.request
what, arg = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
try:
    if what == "read":
        print(open(arg, encoding="utf-8").read())
    elif what == "write":
        os.makedirs(os.path.dirname(arg), exist_ok=True)
        open(arg, "w").write("x")
        print("WROTE")
    elif what == "url":
        print("STATUS", urllib.request.urlopen(arg, timeout=8).status)
    elif what == "socket":
        host, port = arg.rsplit(":", 1)
        socket.create_connection((host, int(port)), timeout=8).close()
        print("CONNECTED")
    elif what == "env":
        print("ENV", os.environ.get(arg, ""))
except Exception as exc:
    print("REFUSED", type(exc).__name__, exc)
'''


def _native_available(engine):
    return not engine._native_sandbox_missing_requirements()


@pytest.fixture
def scratch():
    """A scratch folder with ordinary permissions. pytest's tmp_path (like mkdtemp) is made owner-only, and the
    Windows runner's restricted token cannot start a command inside such a folder."""
    import shutil
    import tempfile
    import uuid
    root = Path(os.environ.get("AGENT8088_ATTACK_ROOT") or tempfile.gettempdir()) / f"agent8088-attack-{uuid.uuid4().hex[:8]}"
    os.makedirs(root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(params=BACKENDS)
def box(request, engine, scratch, monkeypatch):
    """A scratch project, a fake home holding a fake key, and a runner for one backend."""
    tmp_path = scratch
    backend = request.param
    if backend == "native" and not _native_available(engine):
        pytest.skip("the native sandbox is not installed here")
    if backend == "docker" and not engine._docker_available():
        pytest.skip("Docker is not running here")
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text(f"{MARK}-home-key\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / "artifacts").mkdir(parents=True)
    (project / ".env").write_text(f"API_TOKEN={MARK}-dotenv\n", encoding="utf-8")
    (project / "probe.py").write_text(PROBE, encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(engine, "SANDBOX_BACKEND", backend)
    monkeypatch.setattr(engine, "_native_sandbox_broken", False)
    monkeypatch.setattr(engine, "_docker_sandbox_broken", False)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", project / "artifacts")
    monkeypatch.setattr(engine, "_shell_workspace_root", project)
    monkeypatch.setattr(engine, "_sandbox_readonly", False)
    monkeypatch.setattr(engine.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    windows_native = backend == "native" and sys.platform == "win32"
    python = "python" if backend == "docker" else f'"{sys.executable}"'
    if backend == "native":
        # A native sandbox that cannot start falls back to Docker in real use; here that would test the
        # wrong backend, so the native run fails with the reason instead.
        monkeypatch.setattr(engine, "_docker_usable", lambda: False)

    def run(*args, quoted=True):
        parts = " ".join(f'"{a}"' if quoted else a for a in args)
        out = str(engine._run_in_sandbox_backend(backend, f"{python} probe.py {parts}", 120, False, "", project))
        if backend == "native" and engine._native_sandbox_broken:
            pytest.fail(f"the native sandbox could not start here: {engine._native_sandbox_failure[:300]}")
        return out
    return type("Box", (), {"backend": backend, "home": home, "project": project, "outside": outside,
                            "run": staticmethod(run), "windows_native": windows_native, "engine": engine})


def _host_path_for(box, path: Path) -> str:
    """The path a command uses for a host file: Docker sees only /workspace."""
    if box.backend == "docker":
        try:
            return "/workspace/" + path.relative_to(box.project).as_posix()
        except ValueError:
            return path.as_posix()
    return str(path)


def test_the_sandbox_runs_at_all(box):
    # Every other test checks that something did NOT happen; this one makes sure
    # commands really run, so a broken backend cannot pass them all by doing nothing.
    # artifacts/ is writable on every build; the project itself is tested below.
    target = box.project / "artifacts" / "ok.txt"
    out = box.run("write", _host_path_for(box, target))
    if ("WROTE" not in out and box.backend == "docker" and sys.platform.startswith("linux")
            and not hasattr(box.engine, "_clean_stale_docker_sandbox")):
        pytest.xfail("this build runs Docker containers as root with no capabilities, so on Linux they cannot "
                     "write a folder the user owns; the Docker sandbox fixes run them as the host user")
    assert "WROTE" in out and target.exists(), out


def test_a_build_can_write_in_the_project(box):
    if not hasattr(box.engine, "_project_shell_root"):
        pytest.skip("this build does not let commands write the project yet")
    target = box.project / "out" / "ok.txt"
    out = box.run("write", _host_path_for(box, target))
    if ("WROTE" not in out and box.backend == "docker" and sys.platform.startswith("linux")
            and not hasattr(box.engine, "_clean_stale_docker_sandbox")):
        pytest.xfail("Docker containers run as root with no capabilities on this build (see above)")
    assert "WROTE" in out and target.exists(), out


def test_a_write_outside_the_folder_does_not_land(box):
    target = box.outside / "pwned.txt"
    out = box.run("write", str(target))
    assert not target.exists(), out


def test_the_home_folder_key_cannot_be_read(box):
    if box.windows_native:
        pytest.skip("the Windows runner limits writes only; reads are not confined (documented)")
    out = box.run("read", str(box.home / ".ssh" / "id_ed25519"))
    assert MARK not in out, out


def test_a_secret_file_in_the_project_cannot_be_read(box):
    if box.windows_native:
        pytest.skip("the Windows runner limits writes only; reads are not confined (documented)")
    out = box.run("read", _host_path_for(box, box.project / ".env"))
    assert MARK not in out, out


@pytest.mark.parametrize("url", ["https://example.com/", "http://169.254.169.254/latest/meta-data/",
                                 "http://10.0.0.1/", "http://127.0.0.1:9/"])
def test_hosts_off_the_allowlist_are_unreachable(box, monkeypatch, url):
    if box.windows_native:
        pytest.skip("the Windows runner does not filter the network (documented)")
    monkeypatch.setattr(box.engine, "SANDBOX_ALLOWED_DOMAINS", ["pypi.org"])
    out = box.run("url", url)
    assert "STATUS" not in out, out


def test_a_raw_connection_cannot_skip_the_proxy(box):
    if box.windows_native:
        pytest.skip("the Windows runner does not filter the network (documented)")
    out = box.run("socket", "1.1.1.1:443")
    assert "CONNECTED" not in out, out


def test_a_secret_in_the_environment_is_not_inherited(box, monkeypatch):
    if not hasattr(box.engine, "_secret_env_names"):
        pytest.skip("this build does not remove secret variables yet")
    monkeypatch.setenv("ATTACK_SERVICE_TOKEN", f"{MARK}-env")
    monkeypatch.setattr(box.engine, "SANDBOX_PASS_ENV", frozenset())
    out = box.run("env", "ATTACK_SERVICE_TOKEN")
    assert MARK not in out, out


def test_git_hooks_cannot_be_planted(box):
    if not hasattr(box.engine, "_protected_repo_paths"):
        pytest.skip("this build does not let commands write the project yet")
    hooks = box.project / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (box.project / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    out = box.run("write", _host_path_for(box, hooks / "post-checkout"))
    assert not (hooks / "post-checkout").exists(), out


def test_a_symlink_out_of_the_folder_does_not_carry_a_write(box):
    link = box.project / "artifacts" / "escape"
    try:
        if sys.platform == "win32":
            # A directory junction: what any Windows user can make without a privilege.
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(box.outside)], check=True,
                           capture_output=True)
        else:
            os.symlink(box.outside, link, target_is_directory=True)
    except (OSError, NotImplementedError, subprocess.CalledProcessError if sys.platform == "win32" else OSError) as exc:
        pytest.skip(f"a link cannot be made here: {exc}")
    out = box.run("write", _host_path_for(box, link / "pwned.txt"))
    assert not (box.outside / "pwned.txt").exists(), out


def test_a_service_cannot_be_reached_from_the_network(box):
    if not hasattr(box.engine, "_exec_service"):
        pytest.skip("this build has no services yet")
    if box.backend == "native" and sys.platform in ("win32", "darwin"):
        pytest.skip("the service binds its own address on this backend (documented)")
    import socket
    from agent8088 import services
    registry = services.Registry(box.project / "services.json", box.project / "logs")
    box.engine._services_registry = registry
    python = "python" if box.backend == "docker" else f'"{sys.executable}"'
    try:
        out = box.engine._exec_service("service_start", {
            "name": "web", "command": f"{python} -m http.server {{port}} --bind 0.0.0.0"})
        service = registry.get("web")
        assert service is not None and "answering" in out, out
        try:
            lan = socket.gethostbyname(socket.gethostname())
        except OSError:
            pytest.skip("no local network address to try")
        if lan.startswith("127."):
            pytest.skip("no local network address to try")
        with pytest.raises(OSError):
            socket.create_connection((lan, service.handle.host_port), timeout=3).close()
    finally:
        registry.stop_all()


def test_a_service_cannot_reach_hosts_off_the_allowlist(box, monkeypatch):
    if not hasattr(box.engine, "_exec_service"):
        pytest.skip("this build has no services yet")
    if box.windows_native:
        pytest.skip("the Windows runner does not filter the network (documented)")
    from agent8088 import services
    registry = services.Registry(box.project / "services.json", box.project / "logs")
    box.engine._services_registry = registry
    monkeypatch.setattr(box.engine, "SANDBOX_ALLOWED_DOMAINS", ["pypi.org"])
    python = "python" if box.backend == "docker" else f'"{sys.executable}"'
    try:
        box.engine._exec_service("service_start", {"name": "probe", "command":
                                 f"{python} probe.py url https://example.com/ && {python} -m http.server {{port}}"})
        import time
        time.sleep(2)
        service = registry.get("probe")
        log = services.tail(service.log_path) if service else ""
        assert "STATUS" not in log, log
    finally:
        registry.stop_all()
