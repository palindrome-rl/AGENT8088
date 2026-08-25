"""End-to-end coverage for browse_page's new interactive path: a real
browser-use Agent run against a local test page, and a regression check
that a private/loopback target is still refused end-to-end (through the
full _exec_browser path, not just the proxy unit tests in
test_browser_proxy.py) - preserving today's SSRF guarantee.

Three of these four tests launch a real headless Chromium and make real LLM
calls against whatever provider is configured in the test environment, so
they are gated behind the AGENT8088_RUN_BROWSER_INTEGRATION=1 env var (see
_run_live below) and SKIPPED by default. The fourth - the loopback SSRF
regression check - needs neither and always runs.
"""
import http.server
import os
import threading

import pytest

from agent8088 import engine as A

pytestmark = pytest.mark.browser_integration

_run_live = pytest.mark.skipif(
    not os.environ.get("AGENT8088_RUN_BROWSER_INTEGRATION"),
    reason="set AGENT8088_RUN_BROWSER_INTEGRATION=1 to run live browser+LLM integration tests",
)


PAGE_HTML = b"""<!doctype html>
<html><body>
<h1 id="heading">Hello from the test page</h1>
<form id="f" onsubmit="document.getElementById('result').innerText='submitted: ' + document.getElementById('name').value; return false;">
  <input id="name" type="text" />
  <button type="submit">Go</button>
</form>
<p id="result"></p>
<button id="probe" onclick="
  fetch('http://169.254.169.254/latest/meta-data/')
    .then(() => { document.getElementById('probe-result').innerText = 'fetch succeeded'; })
    .catch(() => { document.getElementById('probe-result').innerText = 'fetch blocked'; });
">Probe metadata endpoint</button>
<p id="probe-result"></p>
</body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE_HTML)


@pytest.fixture
def local_test_page():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()
        server.server_close()


@_run_live
def test_browse_page_reads_a_local_page(local_test_page):
    result = A._exec_browser({
        "url": local_test_page,
        "task": "Read the page and report the exact text of the h1 heading.",
    })
    assert "Hello from the test page" in result


@_run_live
def test_browse_page_can_fill_and_submit_a_form(local_test_page):
    result = A._exec_browser({
        "url": local_test_page,
        "task": ("Type 'Ada' into the text input, click the Go button, then "
                  "report the exact text that appears in the result paragraph."),
    })
    assert "submitted: Ada" in result


@_run_live
def test_ssrf_proxy_blocks_a_request_the_page_itself_makes(local_test_page):
    """The pre-flight _egress_check/_ssrf_check in _exec_browser only ever
    sees the *initial* url. This is the one test that proves the SSRF
    proxy - the reason this whole component exists - also governs a request
    the page makes on its own mid-session, not just the first navigation.
    169.254.169.254 (the cloud-metadata address) is a literal IP so this
    needs no real DNS/network access to be deterministic."""
    result = A._exec_browser({
        "url": local_test_page,
        "task": ("Click the 'Probe metadata endpoint' button, wait a moment, "
                  "then report the exact text in the paragraph with id "
                  "'probe-result'."),
    })
    assert "fetch blocked" in result
    assert "fetch succeeded" not in result


def test_browse_page_refuses_a_loopback_target_end_to_end():
    """Needs neither a real browser nor a real model - the SSRF gate in
    _exec_browser runs before any browser-use code, so this one is not
    marked @_run_live and always runs."""
    result = A._exec_browser({
        "url": "http://127.0.0.1:9/",
        "task": "read the page",
    })
    assert "Blocked" in result
