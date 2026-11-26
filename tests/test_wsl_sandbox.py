"""The WSL sandbox distro: paths, the command hand-off, setup and the distro list. No WSL needed."""
import base64
import hashlib
import subprocess
from pathlib import Path

import pytest

from agent8088 import wsl_sandbox as W


@pytest.fixture(autouse=True)
def no_real_wsl(monkeypatch):
    monkeypatch.setattr(W, "wsl_executable", lambda: None)
    monkeypatch.setattr(W, "_download", lambda url, dest: pytest.fail("a unit test tried to download"))


@pytest.mark.parametrize("windows, linux", [
    (r"E:\Palindrome Labs\project", "/mnt/e/Palindrome Labs/project"),
    ("c:/Users/me/app.py", "/mnt/c/Users/me/app.py"),
    ("D:\\", "/mnt/d"),
    ("D:", "/mnt/d"),
])
def test_windows_paths_map_to_mnt(windows, linux):
    assert W.to_linux(windows) == linux


@pytest.mark.parametrize("path", [r"\\server\share\x", "relative\\x", "/usr/bin"])
def test_paths_the_distro_cannot_see_map_to_none(path):
    assert W.to_linux(path) is None


def test_mnt_paths_map_back_to_windows():
    assert W.to_windows("/mnt/e/Palindrome Labs/x.py") == r"E:\Palindrome Labs\x.py"
    assert W.to_windows("/mnt/c") == "C:\\"
    assert W.to_windows("/mnt/cd/x") is None
    assert W.to_windows("/home/agent/x") is None


@pytest.mark.parametrize("command, expected", [
    (r"python E:\proj\app.py --out E:\proj\out", "python /mnt/e/proj/app.py --out /mnt/e/proj/out"),
    (r'cd "C:\Users\First Last\proj" && ls', 'cd "/mnt/c/Users/First Last/proj" && ls'),
    (r"cat E:\\proj\\a.txt", "cat /mnt/e/proj/a.txt"),
    ("ls E:/proj/src", "ls /mnt/e/proj/src"),
])
def test_windows_paths_in_a_command_become_mnt_paths(command, expected):
    assert W.command_to_linux(command) == expected


@pytest.mark.parametrize("command", [
    "curl https://example.com/a:b", "echo a:b", "git log --format=%h:%s", "python -c 'x = {1: 2}'",
    "ls /mnt/e/proj", "echo $HOME:/x",
])
def test_commands_without_windows_paths_are_left_alone(command):
    assert W.command_to_linux(command) == command


def test_a_short_windows_name_is_spelled_long(tmp_path, monkeypatch):
    seen = []

    def normalize(path):
        seen.append(path)
        return path.replace("IMROZE~1.ASL", "imroze.aslam")
    out = W.command_to_linux(r"python C:\Users\IMROZE~1.ASL\x.py", normalize=normalize)
    assert out == "python /mnt/c/Users/imroze.aslam/x.py"
    assert seen == [r"C:\Users\IMROZE~1.ASL\x.py"]


def test_long_path_keeps_a_path_without_a_short_name():
    assert W.long_path(r"Z:\no\such\place") == r"Z:\no\such\place"


def test_the_command_travels_base64_in_wslenv_only():
    env = W.command_env("echo 'a \"b\"' && ls", "/mnt/c/x/srt.json", timeout=30)
    assert base64.b64decode(env[W.ENV_COMMAND]).decode() == "echo 'a \"b\"' && ls"
    assert env[W.ENV_SETTINGS] == "/mnt/c/x/srt.json"
    assert env[W.ENV_TIMEOUT] == "30"
    # WSLENV names only Agent8088's own variables: a user's WSLENV entries are replaced, not kept.
    assert set(env["WSLENV"].split(":")) == {W.ENV_COMMAND, W.ENV_SETTINGS, W.ENV_TIMEOUT}


def test_a_long_command_goes_through_a_file(tmp_path):
    command = "echo " + "x" * 40000
    target = tmp_path / "cmd" / "one.sh"
    env = W.command_env(command, "/mnt/c/s.json", command_file=target)
    assert W.ENV_COMMAND not in env
    assert target.read_text() == command
    assert env[W.ENV_COMMAND_FILE].endswith("/cmd/one.sh") or env[W.ENV_COMMAND_FILE] == ""
    assert W.ENV_COMMAND_FILE in env["WSLENV"]


def test_the_runner_reads_the_command_and_clears_the_hand_off():
    script = W.RUNNER_SCRIPT
    assert "base64 -d" in script and W.SRT_CLI in script
    assert "unset" in script and "WSL_INTEROP" in script and "WSLENV" in script
    assert 'timeout -k 5 "$limit"' in script
    assert "${AGENT8088_WSL_TIMEOUT:-0}" in script


def test_the_command_runs_as_the_unprivileged_user():
    argv = W.command_argv(r"C:\Windows\System32\wsl.exe", "agent8088-sandbox")
    assert argv[argv.index("-u") + 1] == W.USER
    assert argv[-2:] == ["--exec", W.RUNNER]


def test_setup_turns_interop_and_windows_path_off():
    script = W.setup_script("0.0.73")
    assert "[interop]\nenabled=false\nappendWindowsPath=false" in script
    assert "@anthropic-ai/sandbox-runtime@0.0.73" in script
    for package in ("bubblewrap", "socat", "ripgrep", "nodejs", "python3"):
        assert package in script
    assert f"adduser -D -s /bin/bash {W.USER}" in script
    assert "chmod -R go-w /opt/agent8088" in script
    assert script.rstrip().endswith(W.SETUP_MARKER)


def test_setup_installs_pytest():
    # A Python task's check is usually `python -m pytest`. Without pytest in the distro every such check ended
    # "the test tool 'pytest' is not installed where the check ran", and correct work was reported NOT VERIFIED.
    assert "py3-pytest" in W.PACKAGES
    assert "py3-pytest" in W.setup_script("0.0.73")


def test_a_distro_set_up_before_pytest_is_set_up_again():
    # The marker records the setup version; a distro from the older setup no longer counts as ready.
    assert W.SETUP_VERSION != "1"
    old = lambda *a, **k: type("R", (), {"returncode": 0, "stdout": b"1 0.0.73\n"})()  # noqa: E731
    assert not W.is_ready("wsl.exe", "agent8088-sandbox", "0.0.73", run=old)


def test_setup_refuses_a_version_that_is_not_one():
    with pytest.raises(ValueError):
        W.setup_script("1.0; rm -rf /")


def test_the_rootfs_is_pinned_per_processor():
    url, digest = W.rootfs_for_machine("AMD64")
    assert url.endswith(f"alpine-minirootfs-{W.ALPINE_VERSION}-x86_64.tar.gz") and len(digest) == 64
    assert W.rootfs_for_machine("ARM64")[0].endswith("-aarch64.tar.gz")
    assert W.rootfs_for_machine("riscv64") is None


def test_the_distro_list_is_read_from_utf16():
    out = "Ubuntu-22.04\r\ndocker-desktop\r\nagent8088-sandbox\r\n".encode("utf-16-le")
    run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, out, b"")
    assert W.installed_distros("wsl.exe", run) == ["Ubuntu-22.04", "docker-desktop", "agent8088-sandbox"]
    assert W.installed_distros("wsl.exe", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"")) is None


def test_ready_only_with_the_same_setup_and_runtime_version():
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, f"{W.SETUP_VERSION} 0.0.73\n".encode(), b"")
    assert W.is_ready("wsl.exe", "d", "0.0.73", run)
    assert not W.is_ready("wsl.exe", "d", "0.0.74", run)


class FakeWsl:
    def __init__(self, distros):
        self.distros = list(distros)
        self.calls = []
        self.marker = ""

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if argv[1:3] == ["--list", "--quiet"]:
            return subprocess.CompletedProcess(argv, 0, "\r\n".join(self.distros).encode("utf-16-le"), b"")
        if argv[1] == "--import":
            self.distros.append(argv[2])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[-2:] == ["/bin/sh", "-s"]:
            self.script = kw["input"].decode()
            self.marker = f"{W.SETUP_VERSION} 0.0.73"
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[-2:] == ["cat", W.SETUP_MARKER]:
            return subprocess.CompletedProcess(argv, 0 if self.marker else 1, self.marker.encode(), b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def test_provision_imports_a_checked_rootfs_then_sets_it_up(tmp_path, monkeypatch):
    data = b"fake rootfs"
    monkeypatch.setattr(W, "_ROOTFS", {"x86_64": hashlib.sha256(data).hexdigest(),
                                      "aarch64": hashlib.sha256(data).hexdigest()})
    fake = FakeWsl(["Ubuntu-22.04"])
    error = W.provision("agent8088-sandbox", tmp_path / "distro", "0.0.73", wsl="wsl.exe", run=fake,
                        download=lambda url, dest: Path(dest).write_bytes(data))
    assert error is None
    assert "agent8088-sandbox" in fake.distros
    assert any(call[1] == "--terminate" for call in fake.calls)
    # The user's own distro is never touched.
    assert not any("Ubuntu-22.04" in call for call in fake.calls)


def test_provision_refuses_a_rootfs_with_the_wrong_checksum(tmp_path):
    fake = FakeWsl([])
    error = W.provision("agent8088-sandbox", tmp_path / "d", "0.0.73", wsl="wsl.exe", run=fake,
                        download=lambda url, dest: Path(dest).write_bytes(b"tampered"))
    assert "checksum" in error
    assert not any(call[1] == "--import" for call in fake.calls)


def test_provision_keeps_an_existing_distro_and_sets_it_up_again(tmp_path):
    fake = FakeWsl(["agent8088-sandbox"])
    error = W.provision("agent8088-sandbox", tmp_path / "d", "0.0.73", wsl="wsl.exe", run=fake,
                        download=lambda url, dest: pytest.fail("no download for an existing distro"))
    assert error is None
    assert not any(call[1] == "--import" for call in fake.calls)


def test_provision_explains_wsl_that_is_not_set_up(tmp_path):
    run = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"")
    assert "wsl --install" in W.provision("agent8088-sandbox", tmp_path, "0.0.73", wsl="wsl.exe", run=run)


def test_remove_refuses_a_distro_that_is_not_agent8088s():
    assert "Refusing" in W.remove("Ubuntu-22.04", wsl="wsl.exe", run=lambda *a, **k: pytest.fail("ran"))
    ok = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"", b"")
    assert W.remove("agent8088-sandbox-test", wsl="wsl.exe", run=ok) is None
