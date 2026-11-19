"""A stdio MCP server that never answers the handshake must not outlive its
connect timeout. Found live: `agent8088 --version` with such a server in
mcp.json printed instantly but took 301s to exit -- the connect gave up but
the task owning the child process kept running, so exit waited on the child."""
import json
import os
import shutil
import time

import pytest

pytest.importorskip("mcp")

from agent8088.mcp import MCPRuntime


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _sleep_children():
    import subprocess
    out = subprocess.run(["pgrep", "-P", str(os.getpid()), "-f", "sleep 120"],
                         capture_output=True, text=True).stdout.split()
    return [int(pid) for pid in out]


@pytest.mark.skipif(not shutil.which("sleep") or not shutil.which("pgrep"), reason="needs sleep/pgrep")
def test_a_server_that_never_answers_is_killed_after_its_connect_timeout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {
        "hangs": {"command": "sleep", "args": ["120"], "connect_timeout": 1}}}))
    monkeypatch.setenv("AGENT8088_HOME", str(home))
    runtime = MCPRuntime(tmp_path / "project")
    try:
        runtime.reload()
        assert runtime.statuses["hangs"]["state"] == "error"
        deadline = time.monotonic() + 5
        while _sleep_children() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _sleep_children() == [], "the hung server's process outlived its connect timeout"
    finally:
        started = time.monotonic()
        runtime.close()
        assert time.monotonic() - started < 5
        for pid in _sleep_children():
            os.kill(pid, 9)
