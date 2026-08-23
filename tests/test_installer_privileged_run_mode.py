"""install.sh's `_privileged_run_mode`, exercised in isolation.

Before this function existed, the Playwright system-deps install step decided
whether to prepend `sudo` purely from `id -u`, then ran the command with both
stdout and stderr redirected to /dev/null. If sudo needed a real password, it
would still prompt -- sudo opens /dev/tty directly for that, bypassing the
redirect entirely -- but that redirect also meant the "[sudo] password for"
prompt itself was invisible, so from the terminal the installer just went
quiet for up to T_PIP seconds with no visible explanation. `_privileged_run_mode`
exists so the caller can tell "safe to run non-interactively" apart from
"would need to prompt, and a terminal is actually available for that prompt"
*before* attempting the privileged command - the latter case now runs the
prompt for real (unredirected, so it's visible) instead of being skipped.

Same extraction/PATH-isolation convention as test_installer_download_fallback.py:
pull just the one function out of install.sh and run it under a bash whose PATH
holds only stub binaries, so `id`/`sudo` are never the real system ones.

Every test here runs with a real controlling terminal attached (inherited from
whatever ran pytest) - `/dev/tty` is genuinely usable, matching the common case
of a human running the installer directly or via `curl | bash` in their own
terminal. There is no attempt to simulate a fully detached session (a systemd
unit, a CI runner with no pty at all): the [-r]/[-w] test on /dev/tty checks
permission bits, not whether opening it would actually succeed, so it cannot be
forced false just by closing stdin or calling setsid - it would need an
environment where the /dev/tty node itself is unreadable. That gap is pre-existing
(the same check already guards prompt_yes_no and run_agent8088_command) and is
not a hang either way: if sudo's own attempt to open /dev/tty fails there, sudo
fails fast with its own error, which the "prompt" branch's timeout-guarded call
already treats as a failed authentication.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or "/bin/bash"


def _extract(name: str) -> str:
    import re
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}$", source)
    assert match, f"function not found in install.sh: {name}"
    return match.group(0)


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(fake_bin: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # fake_bin only, same reasoning as test_installer_download_fallback.py: if the
    # real PATH were left reachable, a missing `sudo` stub would still resolve to
    # the host's real sudo, and a call to it inside a non-interactive test runner
    # could hang or fail in ways that have nothing to do with what this function
    # is supposed to decide.
    env["PATH"] = str(fake_bin)
    script = _extract("_privileged_run_mode") + "\n_privileged_run_mode"
    return subprocess.run([BASH, "-c", script], env=env,
                           capture_output=True, text=True, timeout=10)


def test_root_runs_direct_without_touching_sudo(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "id", 'if [ "$1" = "-u" ]; then echo 0; fi')
    # No sudo stub at all -- if the function tried to invoke sudo in the root
    # case, this would fail with "command not found" rather than silently
    # succeeding, which would make this test's own bug visible.
    result = _run(fake_bin)
    assert result.stdout.strip() == "direct"


def test_passwordless_sudo_reports_sudo_mode(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "id", 'if [ "$1" = "-u" ]; then echo 1000; fi')
    _write_stub(fake_bin, "sudo", 'if [ "$1" = "-n" ] && [ "$2" = "true" ]; then exit 0; fi; exit 1')
    result = _run(fake_bin)
    assert result.stdout.strip() == "sudo"


def test_sudo_requiring_a_password_prompts_when_a_terminal_is_available(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "id", 'if [ "$1" = "-u" ]; then echo 1000; fi')
    # `sudo -n true` is exactly how a real sudo reports "would need a password"
    # non-interactively: it fails immediately instead of prompting. A real
    # terminal is attached (see module docstring), so this should not skip -
    # skipping here is exactly the silent-stall-avoidance-by-avoidance behaviour
    # the caller is meant to replace with an actual, visible prompt.
    _write_stub(fake_bin, "sudo", 'if [ "$1" = "-n" ] && [ "$2" = "true" ]; then exit 1; fi; exit 1')
    result = _run(fake_bin)
    assert result.stdout.strip() == "prompt"


def test_no_sudo_binary_at_all_skips(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "id", 'if [ "$1" = "-u" ]; then echo 1000; fi')
    result = _run(fake_bin)
    assert result.stdout.strip() == "skip"
