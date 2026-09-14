"""Startup must not touch the network.

An unreachable model endpoint (laptop off the LAN, ollama not running) otherwise
costs ~16.8s on every single CLI invocation -- `agent8088 --help` included --
because the context-window probe ran at import time and the OpenAI SDK retried
it twice by default.
"""
import subprocess
import sys


def _run(script: str) -> subprocess.CompletedProcess:
    """Import the engine in a child process pointed at an unreachable endpoint.

    A reachable endpoint answers fast enough to hide the bug, so the config
    here deliberately names a dead host -- that is the case users hit.
    """
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=120,
        env={**_env()},
    )


def _env() -> dict:
    import os
    env = dict(os.environ)
    env["AGENT8088_CONFIG"] = _dead_config()
    return env


def _dead_config() -> str:
    import tempfile
    import pathlib
    d = pathlib.Path(tempfile.mkdtemp(prefix="a8088-startup-"))
    (d / "config.txt").write_text(
        "default_provider=dead\n"
        "provider.dead.base_url=http://192.0.2.1:8080/v1\n"  # TEST-NET-1, never routable
        "provider.dead.model=nonexistent\n"
        "provider.dead.api_mode=openai\n"
    )
    return str(d / "config.txt")


def test_import_engine_makes_no_socket_connection():
    """Record connect() attempts rather than raising on them.

    The probe catches Exception broadly, so an exception raised inside
    connect() is swallowed and the import still succeeds -- a test that
    raises would pass while the bug is present. Count instead.
    """
    script = (
        "import socket\n"
        "attempts = []\n"
        "_orig = socket.socket.connect\n"
        "def record(self, addr):\n"
        "    attempts.append(addr)\n"
        "    return _orig(self, addr)\n"
        "socket.socket.connect = record\n"
        "import agent8088.engine\n"
        "print('CONNECTS=%d' % len(attempts))\n"
        "print('TARGETS=%r' % (attempts,))\n"
    )
    proc = _run(script)
    assert proc.returncode == 0, proc.stderr[-2000:]
    count = int([l for l in proc.stdout.splitlines()
                 if l.startswith("CONNECTS=")][0].split("=")[1])
    targets = [l for l in proc.stdout.splitlines() if l.startswith("TARGETS=")]
    assert count == 0, (
        f"importing the engine made {count} network connection(s): {targets}"
    )


def test_import_engine_is_fast_against_a_dead_endpoint():
    script = (
        "import time; t = time.time()\n"
        "import agent8088.engine\n"
        "print(time.time() - t)\n"
    )
    proc = _run(script)
    assert proc.returncode == 0, proc.stderr[-2000:]
    elapsed = float(proc.stdout.strip().splitlines()[-1])
    assert elapsed < 3.0, f"engine import took {elapsed:.1f}s against a dead endpoint (budget 3.0s)"
