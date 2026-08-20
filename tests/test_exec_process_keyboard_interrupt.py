"""Ctrl+C during a running shell tool must not hang the whole CLI.

_exec_process() runs the tool's subprocess with start_new_session=True (POSIX)
so a *timeout* can kill just that process group without taking the CLI down
with it. But that same flag detaches the child from the terminal, so it never
receives the Ctrl+C the user pressed. Before this fix, hitting Ctrl+C while a
command was stuck left two problems: the child kept running as an orphan, and
- worse - the whole agent8088 process never exited either, because
Popen.__exit__() unconditionally closes process.stdout to clean up, and that
close() blocks on the same lock the still-running drain() thread holds inside
its still-blocked stdout.read() call. Reproduced by sending a real SIGINT to
this process while _exec_process() is blocked in process.wait(); see the
session's debugging notes for the pty-based version of this same repro.
"""
import os
import signal
import sys
import threading
import time

import pytest

from agent8088 import engine


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery only")
def test_ctrl_c_kills_the_detached_child_and_does_not_hang(tmp_path):
    pidfile = tmp_path / "child.pid"
    command = ["sh", "-c", f"echo $$ > {pidfile}; sleep 30"]

    def send_sigint_soon():
        # Give the child time to start and _exec_process to be blocked in
        # process.wait() before interrupting - too early and we'd interrupt
        # subprocess.Popen() itself instead of exercising the real path.
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=send_sigint_soon, daemon=True).start()

    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        engine._exec_process(command, timeout=30)
    elapsed = time.monotonic() - started

    # Must return almost immediately after the signal, not hang until the
    # subprocess's own 30s sleep finishes on its own.
    assert elapsed < 5, f"took {elapsed:.1f}s - looks like the old hang"

    deadline = time.monotonic() + 2
    child_pid = None
    while time.monotonic() < deadline:
        if pidfile.exists():
            child_pid = int(pidfile.read_text().strip())
            break
        time.sleep(0.05)
    assert child_pid is not None, "child never started"

    deadline = time.monotonic() + 2
    while _pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(child_pid), "orphaned child survived Ctrl+C"
