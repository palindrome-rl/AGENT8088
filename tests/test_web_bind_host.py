"""The web bridge may only bind to loopback.

Every endpoint is unauthenticated, and POST /api/tool/{name} runs any tool in
the registry -- execute_shell included. `agent8088 --web --web-host 0.0.0.0`
therefore handed remote code execution to anyone on the network, and both
web/README.md and the CLI reference advertised exactly that command.

mcp_server.run_mcp_server already refuses a non-loopback bind for the same
reason ("remote MCP requires authentication, which is not configured"); this
pins the same rule for the web server, which exposes strictly more.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.50", "example.internal"])
def test_non_loopback_bind_is_refused(host):
    from agent8088 import web_server

    with pytest.raises(ValueError) as excinfo:
        web_server.run_web_server(host=host, port=8180)
    message = str(excinfo.value).lower()
    assert "localhost" in message or "loopback" in message
    assert "authentication" in message


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_binds_are_accepted(host, monkeypatch):
    """Accepted here means "got past the bind check" -- the server itself is
    never started."""
    from agent8088 import web_server

    started = {}

    def _fake_run(app, host, port, log_level):
        started["host"] = host

    monkeypatch.setattr(web_server, "_frontend_root", lambda: None)
    # Not this test's subject, and it would bind a real port.
    monkeypatch.setattr(web_server, "_ensure_port_free", lambda *a, **k: None)
    monkeypatch.setattr("uvicorn.run", _fake_run)
    # _frontend_root() returning None raises its own RuntimeError about a
    # missing frontend -- which is proof the bind check let the host through.
    with pytest.raises(RuntimeError) as excinfo:
        web_server.run_web_server(host=host, port=8180)
    assert "authentication" not in str(excinfo.value).lower()
