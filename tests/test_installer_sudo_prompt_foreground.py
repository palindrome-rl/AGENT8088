"""install.sh's `run_with_timeout_foreground`, exercised in isolation.

Reproduced live (Docker + tmux, a real pty) before this existed: plain GNU
`timeout` puts its child in a *new* process group -- that's what lets `-k 10`
kill an entire misbehaving subtree, but it also means a child that tries to
read the password from the controlling terminal (`sudo -v`, in the "prompt"
branch of install_deps()'s Playwright-system-deps step) is no longer in the
terminal's foreground process group. The kernel's response to that is
SIGTTIN, whose default action is to STOP the process -- not fail, not prompt
again, just stop, forever, since nothing here ever issues `fg`/SIGCONT. The
typed password just echoes into the tty and is never read. It only ends when
run_with_timeout's own `-k 10` eventually SIGKILLs the stopped process, up to
T_PIP+10s (minutes) later, reporting "sudo authentication failed or timed
out" for what was actually a correct password nobody ever got to consume.

`--foreground` keeps the child in the caller's own process group so it can
actually read the prompt. This suite can't reproduce the SIGTTIN/stop kernel
behavior itself (that needs a real controlling terminal + job control, which
a plain subprocess.run harness does not have) -- that part was verified
manually. What's pinned down here, so it can't silently regress, is the
narrower and fully testable claim: `run_with_timeout_foreground` actually
asks `timeout` for `--foreground`, plain `run_with_timeout` still does not
(losing group-kill there would resurrect the "child ignores SIGTERM and
survives its own timeout" bug -k 10 exists to fix), and both still propagate
exit codes/signal-normalization identically.

Same extraction/PATH-isolation convention as test_installer_download_fallback.py
and test_installer_privileged_run_mode.py.
"""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or "/bin/bash"


def _extract(*names: str) -> str:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    blocks = []
    for name in names:
        match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}$", source)
        assert match, f"function not found in install.sh: {name}"
        blocks.append(match.group(0))
    return "\n".join(blocks)


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# A `timeout` stand-in that both proves what flags it was called with (via a
# log file, since the real functions redirect the wrapped command's own
# stdout/stderr in some call sites) and still actually runs the wrapped
# command, so exit-code propagation can be checked in the same run.
_TIMEOUT_STUB = """
echo "$@" >> "$TIMEOUT_LOG"
while :; do
    case "$1" in
        --foreground) shift ;;
        -k) shift 2 ;;
        *) break ;;
    esac
done
shift  # the seconds argument
exec "$@"
"""


def _run(fake_bin: Path, log_path: Path, extra_script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # fake_bin only -- see test_installer_download_fallback.py's docstring for
    # why a real-PATH fallback would let a regressed implementation pass anyway.
    env["PATH"] = str(fake_bin)
    env["TIMEOUT_LOG"] = str(log_path)
    script = (
        '_TIMEOUT_HAS_K=""\n'
        + _extract("_timeout_supports_k", "run_with_timeout", "run_with_timeout_foreground")
        + "\n"
        + extra_script
    )
    return subprocess.run([BASH, "-c", script], env=env,
                           capture_output=True, text=True, timeout=10)


def test_foreground_variant_asks_timeout_for_foreground(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "timeout", _TIMEOUT_STUB)
    _write_stub(fake_bin, "true", "exit 0")
    log_path = tmp_path / "timeout.log"
    result = _run(fake_bin, log_path,
                  'run_with_timeout_foreground 5 true; echo "rc=$?"')
    assert "rc=0" in result.stdout
    calls = log_path.read_text(encoding="utf-8").splitlines()
    # The probe call (`timeout -k 1 1 true` from _timeout_supports_k) has no
    # --foreground; only the real invocation this test cares about needs it.
    real_calls = [c for c in calls if "5 true" in c]
    assert real_calls, f"expected a real run_with_timeout_foreground call, got: {calls}"
    assert "--foreground" in real_calls[0]


def test_plain_variant_does_not_ask_for_foreground(tmp_path):
    # Regression guard the other direction: every other run_with_timeout caller
    # (npm installs, pip/uv, git clone, ...) relies on `timeout` being able to
    # kill a whole misbehaving process group. Losing that everywhere to fix the
    # sudo-prompt case specifically would trade one hang for another.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "timeout", _TIMEOUT_STUB)
    _write_stub(fake_bin, "true", "exit 0")
    log_path = tmp_path / "timeout.log"
    result = _run(fake_bin, log_path,
                  'run_with_timeout 5 true; echo "rc=$?"')
    assert "rc=0" in result.stdout
    calls = log_path.read_text(encoding="utf-8").splitlines()
    real_calls = [c for c in calls if "5 true" in c]
    assert real_calls, f"expected a real run_with_timeout call, got: {calls}"
    assert "--foreground" not in real_calls[0]


def test_foreground_variant_propagates_nonzero_exit(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_stub(fake_bin, "timeout", _TIMEOUT_STUB)
    _write_stub(fake_bin, "false", "exit 1")
    log_path = tmp_path / "timeout.log"
    result = _run(fake_bin, log_path,
                  'run_with_timeout_foreground 5 false; echo "rc=$?"')
    assert "rc=1" in result.stdout


def test_prompt_branch_uses_the_foreground_variant_for_sudo_v():
    # Static wiring check: the actual fix is only real if the "prompt" case in
    # install_deps() calls the new helper for the interactive `sudo -v`
    # authentication, on both the direct-tty and `curl | bash` (</dev/tty)
    # paths. This is what a future edit reverting to plain run_with_timeout
    # here (e.g. during an unrelated refactor) would silently break.
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^\s*prompt\)\n.*?;;\n", source)
    assert match, "prompt) case not found in install_deps()"
    prompt_block = match.group(0)
    sudo_v_lines = [line for line in prompt_block.splitlines()
                    if re.search(r"run_with_timeout\w* .*sudo -v", line)]
    assert len(sudo_v_lines) == 2, f"expected 2 sudo -v call sites, found: {sudo_v_lines}"
    assert all("run_with_timeout_foreground" in line for line in sudo_v_lines), (
        f"sudo -v must run under run_with_timeout_foreground, not plain "
        f"run_with_timeout: {sudo_v_lines}"
    )
