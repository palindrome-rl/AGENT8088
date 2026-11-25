"""browser_test: the model's own steps, in a real browser, against a service this session runs.

check_app loads a page from files and clicks around by itself; it cannot fill a form, check that a click showed the
right result, or reach a server. browser_test does, and only against a service the session started, with every other
origin refused. The browser tests here run real Chromium against a small server in this process; they are skipped
where no Chromium for Playwright is installed.
"""
import http.server
import json
import threading
from pathlib import Path

import pytest

from agent8088 import browser_test

PAGE = """<!doctype html><html><body>
<h1>Counter</h1><p id="count">0</p><button id="add">Add</button>
<button id="ask">Ask</button><p id="asked"></p>
<form id="f"><input id="name"><select id="size"><option>S</option><option>L</option></select>
<input type="checkbox" id="agree"><button type="submit">Greet</button></form><p id="out"></p>
<div class="spinner" style="display:none">loading</div><ul><li class="row">a</li><li class="row">b</li></ul>
<script src="https://example.com/tracker.js"></script>
<script>
let n = Number(localStorage.getItem('n') || 0); document.getElementById('count').textContent = n;
document.getElementById('add').onclick = () => { n++; localStorage.setItem('n', n);
  document.getElementById('count').textContent = n; };
document.getElementById('ask').onclick = () => { document.getElementById('asked').textContent =
  confirm('Sure?') ? 'yes' : 'no'; };
document.getElementById('f').onsubmit = async (e) => { e.preventDefault();
  const r = await fetch('/api/hello?name=' + encodeURIComponent(document.getElementById('name').value)
    + '&size=' + document.getElementById('size').value + '&agree=' + document.getElementById('agree').checked);
  document.getElementById('out').textContent = (await r.json()).message; };
</script></body></html>"""
BROKEN = "<!doctype html><html><body><p>x</p><script>undefinedFunction()</script></body></html>"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/hello"):
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(self.path).query)
            body = json.dumps({"message": f"Hello, {q['name'][0]} ({q['size'][0]}, {q['agree'][0]})!"}).encode()
            ctype = "application/json"
        elif self.path.startswith("/broken"):
            body, ctype = BROKEN.encode(), "text/html"
        else:
            body, ctype = PAGE.encode(), "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture(scope="module")
def chromium():
    from agent8088 import app_check
    path = app_check.chromium_executable()
    if not path:
        pytest.skip("no Chromium for Playwright is installed here")
    return path


def _run(site, chromium, steps, tmp_path=None, **kw):
    return browser_test.run(site, browser_test.parse_steps(steps), executable=chromium,
                            screenshot_dir=tmp_path, **kw)


# --- steps -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("steps, message", [
    ("not json", "JSON list"),
    ("[]", "non-empty"),
    ('[{"click": "#a", "fill": "#b", "value": 1}]', "exactly one action"),
    ('[{"teleport": "#a"}]', "exactly one action"),
    ('[{"fill": "#a"}]', "needs a value"),
    ('[{"expect_count": ".x", "count": "3"}]', "integer count"),
    ('["click"]', "not an object"),
])
def test_bad_steps_are_explained(steps, message):
    with pytest.raises(ValueError, match=message):
        browser_test.parse_steps(steps)


def test_too_many_steps_are_refused():
    with pytest.raises(ValueError, match="at most"):
        browser_test.parse_steps([{"wait": 1}] * (browser_test.MAX_STEPS + 1))


# --- a real browser ------------------------------------------------------------------------------------------------


def test_a_full_scenario_passes(site, chromium, tmp_path):
    report = _run(site, chromium, [
        {"expect_text": "Counter"}, {"click": "#add"}, {"click": "#add"}, {"expect_text": "2", "selector": "#count"},
        {"reload": True}, {"expect_text": "2", "selector": "#count"},
        {"fill": "#name", "value": "Ada"}, {"select": "#size", "value": "L"}, {"check": "#agree"},
        {"press": "Enter", "selector": "#name"}, {"wait_for": "#out"},
        {"expect_text": "Hello, Ada (L, true)!", "selector": "#out"},
        {"expect_count": ".row", "count": 2}, {"expect_hidden": ".spinner"}, {"expect_no_text": "Error"},
        {"click": "#ask"}, {"expect_text": "yes", "selector": "#asked"}, {"expect_url": "127.0.0.1"},
    ], tmp_path)
    assert report.passed, report.text()
    assert report.checks == 9
    text = report.text()
    assert text.startswith("browser test PASSED: 18 step(s)") and "9 expectation(s) met" in text
    assert Path(report.screenshot).is_file()


def test_a_wrong_result_fails_with_what_the_page_shows(site, chromium):
    report = _run(site, chromium, [{"expect_text": "Counter"}, {"expect_text": "99", "selector": "#count"},
                                   {"click": "#add"}])
    assert not report.passed and len(report.steps) == 2   # stopped at the failing step
    assert report.text().startswith("browser test FAILED at step 2 (expect_text)")
    assert "the page shows" in report.steps[1].detail


def test_a_missing_element_is_named(site, chromium):
    report = _run(site, chromium, [{"click": "#nope"}], step_timeout=1.5)
    assert not report.passed and "#nope matched no element" in report.steps[0].detail


def test_other_origins_are_refused_and_not_counted_as_the_apps_errors(site, chromium):
    report = _run(site, chromium, [{"expect_text": "Counter"}])
    assert "https://example.com/tracker.js" in report.blocked
    assert not any("ERR_FAILED" in e for e in report.console_errors)
    assert report.passed


def test_a_test_cannot_leave_its_service(site, chromium):
    report = _run(site, chromium, [{"goto": "https://example.com/"}])
    assert not report.passed and "a test stays on its service" in report.steps[0].detail
    report = _run(site, chromium, [{"goto": "/broken"}, {"expect_text": "x"}])
    assert report.steps[0].ok   # a path of the same service is fine


def test_a_page_error_fails_the_test(site, chromium):
    report = _run(site + "broken", chromium, [{"expect_text": "x"}])
    assert not report.passed and any("undefinedFunction" in e for e in report.page_errors)


def test_a_test_without_expectations_says_it_proves_nothing(site, chromium):
    report = _run(site, chromium, [{"click": "#add"}])
    assert report.passed and "No expect_* step" in report.text()


def test_a_page_that_will_not_open_is_not_a_result_about_the_app(chromium):
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    report = browser_test.run(f"http://127.0.0.1:{port}/", browser_test.parse_steps([{"expect_text": "x"}]),
                              executable=chromium)
    assert report.error and not report.passed and report.text().startswith("browser test could not run")


# --- the engine ---------------------------------------------------------------------------------------------------


@pytest.fixture
def registry(engine, tmp_path, monkeypatch):
    from agent8088 import services
    registry = services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=4242)
    monkeypatch.setattr(engine, "_services_registry", registry)
    return registry


@pytest.mark.parametrize("url, message", [
    ("", "needs the url"),
    ("https://example.com/", "is not one"),
    ("http://192.168.1.5:8000/", "is not one"),
    ("file:///etc/passwd", "is not one"),
    ("http://127.0.0.1/", "is not one"),
    ("http://127.0.0.1:9/", "nothing this session started answers on port 9"),
])
def test_only_a_running_service_of_this_session_can_be_tested(engine, registry, url, message):
    assert message in engine._browser_test_target_error(url)


def test_a_running_service_can_be_tested(engine, registry):
    from agent8088 import services
    registry.services["web"] = services.Service(
        "web", "x", "main", 5555, "native", Path("log"),
        services.Handle(stop=lambda: None, alive=lambda: True, host_port=5555))
    assert engine._browser_test_target_error("http://127.0.0.1:5555/login") == ""
    assert engine._browser_test_target_error("http://localhost:5555/") == ""
    registry.services["web"].handle.alive = lambda: False
    assert "nothing this session started" in engine._browser_test_target_error("http://127.0.0.1:5555/")


def test_a_refused_address_never_reaches_the_browser(engine, registry, monkeypatch):
    monkeypatch.setattr(engine, "_exec_browser_test", lambda args: pytest.fail("the browser must not start"))
    out = engine.run_tool("browser_test", {"url": "http://127.0.0.1:9/", "steps": '[{"expect_text": "x"}]'})
    assert out.startswith("Error: nothing this session started")


@pytest.mark.parametrize("text, status", [
    ("browser test PASSED: 3 step(s) on x, 2 expectation(s) met.", "passed"),
    ("browser test PASSED: 1 step(s) on x, 0 expectation(s) met. (No expect_* step: ...)", "unknown"),
    ("browser test FAILED at step 2 (expect_text): ...", "failed"),
    ("browser test could not run: no Chromium", "unknown"),
])
def test_the_result_is_evidence_like_a_test_run(engine, text, status):
    wrapped = engine._wrap_untrusted(text, "browser test")
    assert engine._browser_test_status("browser_test", wrapped, "unknown") == status
    assert engine._browser_test_status("check_app", wrapped, "unknown") == "unknown"


def test_it_is_a_browser_tool_so_restricted_modes_ask_first(engine):
    assert engine.TOOL_SPECS["browser_test"]["mode"] == "browser"
    engine.PERMISSION_MODE = "readonly"
    assert engine.check_permission("browser") is False
    engine.PERMISSION_MODE = "plan-only"
    assert engine.check_permission("browser") is False


def test_without_a_browser_it_says_how_to_get_one(engine, registry, monkeypatch):
    monkeypatch.setattr(engine, "_playwright_chromium_executable", lambda: None)
    out = engine._exec_browser_test({"url": "http://127.0.0.1:5555/", "steps": '[{"expect_text": "x"}]'})
    assert "playwright install chromium" in out
