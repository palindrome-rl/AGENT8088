"""A page that calls a service on this machine (Ollama on :11434, a local API) is not a hotlinked font.

The page check blocks every other host, so that call is never made. A page that ignores a failed reply and falls back to a
heuristic still passes the check, which is how an app whose requests were all rejected (HTTP 400) was reported as exercised.
The warning now says the call was not verified and how to verify it."""
import pytest

from agent8088 import app_check
from tests.test_app_check import _page, chromium, needs_chromium  # noqa: F401

CALLS_OLLAMA = """<!doctype html><html><body>
<button id="go" onclick="fetch('http://localhost:11434/v1/systemone', {method: 'POST', body: '{}'}).catch(() => {});
document.getElementById('out').textContent='asked'">Go</button><p id="out">waiting</p></body></html>"""

CALLS_CDN = """<!doctype html><html><body>
<button id="go" onclick="fetch('https://cdn.example.com/x.json').catch(() => {});
document.getElementById('out').textContent='asked'">Go</button><p id="out">waiting</p></body></html>"""


def test_local_service_call_is_called_out():
    text = app_check._blocked_request_warning("http://localhost:11434/v1/systemone", {"POST"})
    assert "local service" in text and "11434" in text and "POST" in text and "NOT verified" in text
    assert "shell command" in text


def test_loopback_forms_are_recognised():
    for url in ("http://127.0.0.1:3000/api/x", "http://[::1]:8000/x", "http://0.0.0.0:5000/"):
        assert "local service" in app_check._blocked_request_warning(url, set())
    assert "http://[::1]:8000/x" in app_check._blocked_request_warning("http://[::1]:8000/x", {"GET"})


def test_ordinary_external_host_keeps_the_old_wording():
    assert app_check._blocked_request_warning("https://cdn.example.com/x.js", {"GET"}) == \
        "blocked request to an external host: https://cdn.example.com/x.js"
    assert app_check._blocked_request_warning("http://localhost/x", {"GET"}).startswith("blocked request to an external host")


@needs_chromium
def test_page_that_calls_ollama_gets_the_local_service_warning(tmp_path, chromium):
    report = app_check.check_web_page(_page(tmp_path, CALLS_OLLAMA))
    assert report.status == app_check.PASS
    assert any("local service" in w and "11434" in w and "POST" in w for w in report.warnings), report.warnings


@needs_chromium
def test_page_that_calls_a_cdn_keeps_the_old_warning(tmp_path, chromium):
    report = app_check.check_web_page(_page(tmp_path, CALLS_CDN))
    assert any(w.startswith("blocked request to an external host") for w in report.warnings), report.warnings
