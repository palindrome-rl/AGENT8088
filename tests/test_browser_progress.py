import io
import time

from rich.console import Console

from agent8088 import cli


def test_browse_status_shows_the_current_host(monkeypatch):
    monkeypatch.setattr(cli.A, "browser_status", lambda: "example.com")
    output = io.StringIO()

    Console(file=output, width=80, color_system=None).print(
        cli._StatusLine("running browse_page...", time.time(), [0], interruptible=False))

    assert "visiting example.com" in output.getvalue()
