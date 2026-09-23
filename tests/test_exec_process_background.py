"""_exec_process must return once bash exits, even if a background child
inherited stdout and is still running (a server started with `cmd &`)."""
import os
import signal
import time


def test_background_child_holding_stdout_does_not_hang(engine):
    started = time.monotonic()
    out = engine._exec_process("sleep 30 & echo started; echo $! > /tmp/_a8088_bg_pid",
                               timeout=20, shell=True)
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 10, elapsed
        assert "started" in out
        assert "background process" in out
    finally:
        try:
            os.kill(int(open("/tmp/_a8088_bg_pid").read().strip()), signal.SIGKILL)
        except (OSError, ValueError):
            pass
        try:
            os.remove("/tmp/_a8088_bg_pid")
        except OSError:
            pass


def test_background_child_survives_the_call(engine, tmp_path):
    pid_file = tmp_path / "pid"
    engine._exec_process(f"sleep 30 & echo $! > {pid_file}", timeout=20, shell=True)
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)  # raises if the child was killed with the call
    finally:
        os.kill(pid, signal.SIGKILL)


def test_redirected_background_child_returns_plain_output(engine, tmp_path):
    pid_file = tmp_path / "pid"
    out = engine._exec_process(f"sleep 30 > /dev/null 2>&1 & echo $! > {pid_file}; echo ok",
                               timeout=20, shell=True)
    try:
        assert out == "ok"
    finally:
        os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)
