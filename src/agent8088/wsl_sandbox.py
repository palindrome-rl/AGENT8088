"""Sandboxed commands on Windows through Agent8088's own WSL distro.

The Windows ACL runner limits file writes only: it filters no network, shares the host's process list, and blocks a
child process with piped output, which stops Node build tools. On Windows with WSL 2, Agent8088 instead imports a small
Alpine distro of its own and runs each command there under srt, the same runtime and settings as on Linux.

The distro is Agent8088's alone, never a distro the user works in, so it can be set up for the sandbox:

- WSL interop is off and Windows' PATH is not appended. Interop lets any Linux process start a Windows program
  (cmd.exe, powershell.exe) with the user's full rights, outside every Linux boundary.
- Commands run as an ordinary user, `agent`. srt, the runner script and the package tree are root's and read-only.
- Nothing from the Windows environment reaches a command except the variables listed in WSLENV, which Agent8088 sets
  to its own. A token in the user's shell never enters the distro.

Windows drives are mounted at /mnt/<letter>, as in any distro. srt makes the whole filesystem read-only except the
folders a command may write, and hides the user's secret files and folders, given as /mnt paths.

Nothing here runs a sandboxed command itself. engine.py builds the settings and the command and calls command_argv()
and command_env(); this module owns the distro, the path mapping and the setup.
"""
from __future__ import annotations

import base64
import hashlib
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path, PureWindowsPath
from typing import Callable

DISTRO = "agent8088-sandbox"
USER = "agent"
RUNTIME_DIR = "/opt/agent8088"
SRT_CLI = f"{RUNTIME_DIR}/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js"
RUNNER = f"{RUNTIME_DIR}/run"
# A marker file the setup writes last; a distro without it was imported but never finished setting up.
SETUP_MARKER = f"{RUNTIME_DIR}/setup-version"
# Bumped when the setup script changes, so an older distro is set up again.
SETUP_VERSION = "1"

ALPINE_VERSION = "3.24.4"
_ROOTFS = {
    "x86_64": "cdaa38cb8de3537967ee5b6c9b6cd00cc962da2b018e6eea48696bb274923012",
    "aarch64": "4b14c18f7e90a063b61462ea1462a5732c154bf6adad594974fe6487cd98a8de",
}
PACKAGES = ("bash", "nodejs", "npm", "python3", "py3-pip", "git", "bubblewrap", "socat", "ripgrep")

# The command arrives in an environment variable, not on wsl.exe's command line: wsl.exe re-splits its command line
# with its own rules, so a quoted command crossing it would not arrive as written. A command too long for one
# variable is written to a file instead.
ENV_COMMAND = "AGENT8088_WSL_COMMAND"
ENV_COMMAND_FILE = "AGENT8088_WSL_COMMAND_FILE"
ENV_SETTINGS = "AGENT8088_WSL_SETTINGS"
ENV_TIMEOUT = "AGENT8088_WSL_TIMEOUT"
ENV_SERVICE = "AGENT8088_WSL_SERVICE"
# Services: the sandboxed side listens on a unix socket here (the distro's own /tmp: drvfs has no unix sockets), and
# a bridge outside srt but inside the distro listens on 127.0.0.1:<port>, which WSL forwards to Windows' 127.0.0.1.
SERVICE_DIR = "/tmp/agent8088-services"
STOPPER = f"{RUNTIME_DIR}/stop"
_SERVICE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")
_MAX_ENV_COMMAND = 24000   # Windows allows 32767 characters for one variable; leave room for base64 and the rest

WSL_CONF = """\
# Written by Agent8088. This distro runs Agent8088's sandboxed commands and nothing else.
[interop]
enabled=false
appendWindowsPath=false
[automount]
enabled=true
options=metadata
[user]
default=agent
"""

# Runs for every command, as `agent`. `timeout` ends the command inside the distro too: stopping wsl.exe on Windows
# does not reliably stop what it started.
RUNNER_SCRIPT = f"""\
#!/bin/sh
# Written by Agent8088: runs one sandboxed command under srt. See wsl_sandbox.py.
set -e
if [ -n "${ENV_COMMAND_FILE}" ]; then
  cmd=$(cat "${ENV_COMMAND_FILE}")
else
  cmd=$(printf %s "${ENV_COMMAND}" | base64 -d)
fi
settings="${ENV_SETTINGS}"
limit="${{{ENV_TIMEOUT}:-0}}"
service="${ENV_SERVICE}"
unset {ENV_COMMAND} {ENV_COMMAND_FILE} {ENV_SETTINGS} {ENV_TIMEOUT} {ENV_SERVICE} WSL_INTEROP WSLENV
cd /
if [ -n "$service" ]; then
  # A service: its pid, so `stop` can end it (srt's sandbox dies with it), and no time limit.
  echo $$ > {SERVICE_DIR}/"$service".pid
  exec node {SRT_CLI} --settings "$settings" -c "$cmd"
fi
if [ "$limit" -gt 0 ] 2>/dev/null; then
  exec timeout -k 5 "$limit" node {SRT_CLI} --settings "$settings" -c "$cmd"
fi
exec node {SRT_CLI} --settings "$settings" -c "$cmd"
"""


# Ends one service: the runner's pid (srt, whose sandbox dies with it), then the bridge serving its socket.
STOPPER_SCRIPT = f"""\
#!/bin/sh
# Written by Agent8088: stops one service started by the runner. See wsl_sandbox.py.
case "$1" in ''|*[!a-z0-9-]*) exit 2;; esac
pidfile={SERVICE_DIR}/"$1".pid
if [ -f "$pidfile" ]; then
  pid=$(cat "$pidfile")
  kill -TERM "$pid" 2>/dev/null || true
  sleep 1
  kill -KILL "$pid" 2>/dev/null || true
fi
pkill -f "UNIX-CONNECT:{SERVICE_DIR}/$1.sock" 2>/dev/null || true
rm -f "$pidfile" {SERVICE_DIR}/"$1".sock
"""


def setup_script(srt_version: str) -> str:
    """The root shell script that sets the distro up (or brings it up to date). Safe to run again."""
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", srt_version):
        raise ValueError(f"not a sandbox runtime version: {srt_version!r}")
    return f"""\
set -e
cat > /etc/wsl.conf <<'AGENT8088_CONF'
{WSL_CONF}AGENT8088_CONF
apk upgrade --no-cache >/dev/null
apk add --no-cache {' '.join(PACKAGES)} >/dev/null
id {USER} >/dev/null 2>&1 || adduser -D -s /bin/bash {USER}
ln -sf /usr/bin/python3 /usr/local/bin/python
ln -sf /usr/bin/pip3 /usr/local/bin/pip
mkdir -p {RUNTIME_DIR}
cd {RUNTIME_DIR}
[ -f package.json ] || printf '{{"name":"agent8088-wsl-runtime","private":true}}\\n' > package.json
npm install --no-audit --no-fund --silent @anthropic-ai/sandbox-runtime@{srt_version} >/dev/null 2>&1
cat > {RUNNER} <<'AGENT8088_RUNNER'
{RUNNER_SCRIPT}AGENT8088_RUNNER
cat > {STOPPER} <<'AGENT8088_STOPPER'
{STOPPER_SCRIPT}AGENT8088_STOPPER
chown -R root:root {RUNTIME_DIR}
chmod -R go-w {RUNTIME_DIR}
chmod 755 {RUNNER} {STOPPER}
test -f {SRT_CLI}
printf '%s %s\\n' {SETUP_VERSION} {srt_version} > {SETUP_MARKER}
"""


# ---- paths -------------------------------------------------------------------------------------------------------

_DRIVE_RE = re.compile(r"^([A-Za-z]):(?:[\\/]|$)")
_MNT_RE = re.compile(r"^/mnt/([a-z])(?=/|$)(.*)$")


def to_linux(path) -> str | None:
    """`E:\\a\\b` -> `/mnt/e/a/b`; None for a path the distro cannot see (a UNC share, a relative path)."""
    text = str(path)
    match = _DRIVE_RE.match(text)
    if not match:
        return None
    rest = text[2:].replace("\\", "/").strip("/")
    return f"/mnt/{match.group(1).lower()}" + (f"/{rest}" if rest else "")


def to_windows(path: str) -> str | None:
    """`/mnt/e/a/b` -> `E:\\a\\b`; None for any other path (it exists only inside the distro)."""
    match = _MNT_RE.match(str(path or "").replace("\\", "/"))
    if not match:
        return None
    rest = match.group(2).strip("/")
    return str(PureWindowsPath(f"{match.group(1).upper()}:\\", *([rest] if rest else [])))


# A Windows path in a shell command, quoted (spaces allowed) or bare. The look-behind keeps a URL's `p://`, a
# `--flag=C:\x` value aside, from being taken apart: a drive letter has to start a word.
_QUOTED_PATH_RE = re.compile(r"""(?P<q>["'])(?P<drive>[A-Za-z]):(?P<rest>[\\/][^"'\n]*)(?P=q)""")
_BARE_PATH_RE = re.compile(r"""(?<![\w/\\.:-])(?P<drive>[A-Za-z]):(?P<rest>[\\/][^\s"'`;&|<>()]*)""")


def _linux_spelling(drive: str, rest: str, normalize: Callable[[str], str] | None = None) -> str:
    if normalize is not None:
        windows = normalize(f"{drive}:" + re.sub(r"\\\\|/", "\\\\", rest))
        drive, rest = windows[0], windows[2:]
    tail = re.sub(r"\\\\|\\|/+", "/", rest).strip("/")
    return f"/mnt/{drive.lower()}" + (f"/{tail}" if tail else "")


def long_path(path: str) -> str:
    """`C:\\Users\\IMROZE~1.ASL\\x` -> `C:\\Users\\imroze.aslam\\x`: the longest existing part, resolved.

    Windows answers to a folder's 8.3 short name too, and so does the distro's /mnt, but srt's rules are mounts
    on the long spelling: a write through the short one met no writable mount and was refused. Only paths with a
    `~` are looked at; anything that cannot be resolved is returned as it was."""
    if "~" not in path:
        return path
    current, rest = Path(path), []
    while True:
        try:
            if current.exists():
                resolved = current.resolve()
                return str(resolved.joinpath(*reversed(rest))) if rest else str(resolved)
        except OSError:
            return path
        if current.parent == current:
            return path
        rest.append(current.name)
        current = current.parent


def command_to_linux(command: str, normalize: Callable[[str], str] | None = long_path) -> str:
    """Rewrite Windows paths in a shell command to the distro's /mnt paths.

    The file tools and their results speak Windows paths (`E:\\project\\app.py`), and the model passes them on to
    the shell, where bash would read `E:\\project\\app.py` as `E:projectapp.py`. Paths escaped once for JSON
    (`E:\\\\project`) are rewritten the same way.
    """
    command = _QUOTED_PATH_RE.sub(
        lambda m: m.group("q") + _linux_spelling(m.group("drive"), m.group("rest"), normalize) + m.group("q"),
        command)
    return _BARE_PATH_RE.sub(lambda m: _linux_spelling(m.group("drive"), m.group("rest"), normalize), command)


# ---- the distro --------------------------------------------------------------------------------------------------

def wsl_executable() -> str | None:
    """wsl.exe, or None (not Windows, or WSL is not installed)."""
    found = shutil.which("wsl.exe") or shutil.which("wsl")
    return found if found and PureWindowsPath(found).suffix.lower() == ".exe" else None


def _decode(output: bytes) -> str:
    """wsl.exe writes its own messages in UTF-16; what the Linux side prints is UTF-8."""
    if not output:
        return ""
    if b"\x00" in output[:200]:
        return output.decode("utf-16-le", errors="replace").replace("\ufeff", "")
    return output.decode("utf-8", errors="replace")


def installed_distros(wsl: str, run: Callable = subprocess.run) -> list[str] | None:
    """The distro names WSL knows, or None when wsl.exe cannot list them (WSL not set up)."""
    try:
        result = run([wsl, "--list", "--quiet"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in _decode(result.stdout).splitlines() if line.strip()]


def setup_state(wsl: str, distro: str, run: Callable = subprocess.run) -> str:
    """What the distro's setup marker says ("<setup version> <srt version>"), or "" when it is not set up."""
    try:
        result = run([wsl, "-d", distro, "-u", "root", "--exec", "cat", SETUP_MARKER],
                     capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return _decode(result.stdout).strip() if result.returncode == 0 else ""


def is_ready(wsl: str, distro: str, srt_version: str, run: Callable = subprocess.run) -> bool:
    """The distro is there and set up by this version of the setup, with this srt."""
    return setup_state(wsl, distro, run) == f"{SETUP_VERSION} {srt_version}"


def rootfs_for_machine(machine: str | None = None) -> tuple[str, str] | None:
    """(download URL, sha256) of the pinned Alpine root filesystem for this processor, or None."""
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        (machine or platform.machine()).lower())
    if arch is None:
        return None
    branch = ".".join(ALPINE_VERSION.split(".")[:2])
    url = (f"https://dl-cdn.alpinelinux.org/alpine/v{branch}/releases/{arch}/"
           f"alpine-minirootfs-{ALPINE_VERSION}-{arch}.tar.gz")
    return url, _ROOTFS[arch]


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=300) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)


def provision(distro: str, install_dir: Path, srt_version: str, *, wsl: str | None = None,
              run: Callable = subprocess.run, download: Callable = _download,
              log: Callable = lambda message: None) -> str | None:
    """Import the distro (when missing) and set it up. Returns an error message, or None when it is ready.

    Only ever imports a distro under Agent8088's own name and never changes another one. The root filesystem is
    checked against its pinned sha256 before it is imported.
    """
    wsl = wsl or wsl_executable()
    if not wsl:
        return "WSL is not installed (no wsl.exe)."
    distros = installed_distros(wsl, run)
    if distros is None:
        return ("WSL is installed but is not set up: `wsl --list` failed. Run `wsl --install --no-distribution` "
                "as an administrator and restart Windows.")
    if distro not in distros:
        rootfs = rootfs_for_machine()
        if rootfs is None:
            return f"No Alpine root filesystem is pinned for this processor ({platform.machine()})."
        url, expected = rootfs
        install_dir = Path(install_dir)
        with tempfile.TemporaryDirectory(prefix="agent8088-wsl-") as tmp:
            archive = Path(tmp) / "rootfs.tar.gz"
            log(f"Downloading Alpine Linux {ALPINE_VERSION} for the WSL sandbox...")
            try:
                download(url, archive)
            except OSError as exc:
                return f"Could not download the WSL sandbox's Alpine root filesystem: {exc}"
            if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                return "The downloaded Alpine root filesystem failed its checksum; it was not imported."
            install_dir.mkdir(parents=True, exist_ok=True)
            log(f"Importing the WSL distro {distro}...")
            result = run([wsl, "--import", distro, str(install_dir), str(archive), "--version", "2"],
                         capture_output=True, timeout=600)
            if result.returncode != 0:
                return f"wsl --import failed: {_decode(result.stdout + result.stderr).strip()[:300]}"
    log("Setting up the WSL sandbox (Node, Python, git, bubblewrap, socat, ripgrep, sandbox runtime)...")
    try:
        result = run([wsl, "-d", distro, "-u", "root", "--exec", "/bin/sh", "-s"],
                     input=setup_script(srt_version).encode("utf-8"), capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        return "Setting up the WSL sandbox timed out after 15 minutes."
    if result.returncode != 0:
        return f"Setting up the WSL sandbox failed: {_decode(result.stdout + result.stderr).strip()[-400:]}"
    # wsl.conf (interop off, no Windows PATH) applies from the distro's next start.
    run([wsl, "--terminate", distro], capture_output=True, timeout=60)
    if not is_ready(wsl, distro, srt_version, run):
        return "The WSL sandbox was set up but its setup marker is missing."
    return None


def remove(distro: str, wsl: str | None = None, run: Callable = subprocess.run) -> str | None:
    """Unregister Agent8088's distro (deletes its disk). Refuses any other name."""
    if distro != DISTRO and not distro.startswith(DISTRO + "-"):
        return f"Refusing to remove {distro!r}: it is not an Agent8088 sandbox distro."
    wsl = wsl or wsl_executable()
    if not wsl:
        return "WSL is not installed (no wsl.exe)."
    result = run([wsl, "--unregister", distro], capture_output=True, timeout=300)
    return None if result.returncode == 0 else _decode(result.stdout + result.stderr).strip()[:300]


# ---- one command -------------------------------------------------------------------------------------------------

def command_argv(wsl: str, distro: str) -> list[str]:
    """wsl.exe argv that runs the runner script as `agent`. The command itself travels in command_env()."""
    return [wsl, "-d", distro, "-u", USER, "--cd", "/", "--exec", RUNNER]


def service_name(name: str) -> str:
    """A name safe for the service's pid and socket files (and for wsl.exe's command line)."""
    if not _SERVICE_NAME_RE.fullmatch(name or ""):
        raise ValueError(f"not a WSL service name: {name!r}")
    return name


def service_socket(name: str) -> str:
    return f"{SERVICE_DIR}/{service_name(name)}.sock"


def prepare_services_argv(wsl: str, distro: str) -> list[str]:
    """Makes the services folder (owned by `agent`, private) before a service's sandbox needs it."""
    return [wsl, "-d", distro, "-u", USER, "--exec", "install", "-d", "-m", "700", SERVICE_DIR]


def bridge_argv(wsl: str, distro: str, host_port: int, name: str) -> list[str]:
    """127.0.0.1:<host_port> in the distro (forwarded to Windows by WSL) -> the service's unix socket."""
    return [wsl, "-d", distro, "-u", USER, "--exec", "socat",
            f"TCP-LISTEN:{int(host_port)},bind=127.0.0.1,reuseaddr,fork", f"UNIX-CONNECT:{service_socket(name)}"]


def stop_argv(wsl: str, distro: str, name: str) -> list[str]:
    return [wsl, "-d", distro, "-u", USER, "--exec", STOPPER, service_name(name)]


def command_env(command: str, settings_linux: str, timeout: int = 0,
                command_file: Path | None = None, service: str = "") -> dict:
    """The variables wsl.exe passes in for one command, WSLENV included.

    WSLENV is replaced, not extended: it is the list of Windows variables the distro may see, and a user's own
    WSLENV could name a token. `command_file` is where a command too long for a variable is written; it must be a
    folder the sandboxed command cannot write.
    """
    env = {ENV_SETTINGS: settings_linux, ENV_TIMEOUT: str(max(0, int(timeout or 0)))}
    if service:
        env[ENV_SERVICE] = service_name(service)
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    if len(encoded) <= _MAX_ENV_COMMAND or command_file is None:
        env[ENV_COMMAND] = encoded
    else:
        command_file.parent.mkdir(parents=True, exist_ok=True)
        command_file.write_bytes(command.encode("utf-8"))
        env[ENV_COMMAND_FILE] = to_linux(command_file.resolve()) or ""
    env["WSLENV"] = ":".join(name for name in env if name != "WSLENV")
    return env
