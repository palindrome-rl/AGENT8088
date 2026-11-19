"""browse_page without Chromium/Playwright: read-only tasks get the page's
static HTML text through the same guarded fetch as every other download
(_fetch_url_bytes: egress + SSRF checks, redirect re-checks, pinned address,
size cap); interactive tasks get the install error as before; BROWSER is
reported degraded. No network: the fetch is stubbed."""

import pytest

from agent8088 import capabilities

PAGE = (b"<html><head><title>Docs</title><style>.x{}</style>"
        b"<script>alert('x')</script></head><body><h1>Install</h1>"
        b"<p>Run <b>make</b> first.</p><noscript>enable js</noscript></body></html>")


@pytest.fixture
def no_chromium(engine, monkeypatch):
    fetched = []

    def _fetch(url):
        fetched.append(url)
        return PAGE, None, "text/html"

    monkeypatch.setattr(engine, "_fetch_url_bytes", _fetch)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    monkeypatch.setattr(engine, "_browser_use_available", lambda: True)
    monkeypatch.setattr(engine, "_set_browser_use_log_verbosity", lambda *a: None)
    monkeypatch.setattr(engine, "_playwright_chromium_executable", lambda: None)
    return engine, fetched


def test_read_only_task_gets_static_text_with_note(no_chromium):
    engine, fetched = no_chromium
    out = engine._exec_browser({"url": "https://docs.example/", "task": "summarize this page"})
    assert fetched == ["https://docs.example/"]
    assert "Title: Docs" in out and "Install" in out and "Run make first." in out
    assert "alert" not in out and ".x{}" not in out and "enable js" not in out
    assert "<<<EXTERNAL_UNTRUSTED_CONTENT" in out
    assert "[note: static HTML fetch — no JavaScript, no interaction; Chromium isn't installed" in out
    assert "-m playwright install chromium" in out
    entry = capabilities.get(capabilities.BROWSER)
    assert entry.state == capabilities.DEGRADED and entry.active == "static HTML fetch"


@pytest.mark.parametrize("task", ["click the login button", "fill the form and submit",
                                  "log in with my account"])
def test_interactive_task_is_refused_without_fetching(no_chromium, task):
    engine, fetched = no_chromium
    out = engine._exec_browser({"url": "https://docs.example/", "task": task})
    assert "Chromium browser is not installed" in out
    assert fetched == []
    assert capabilities.get(capabilities.BROWSER).state == capabilities.DEGRADED


def test_ssrf_guard_still_runs_first(engine, monkeypatch):
    fetched = []
    monkeypatch.setattr(engine, "_fetch_url_bytes", lambda url: fetched.append(url))
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)
    out = engine._exec_browser({"url": "http://127.0.0.1:9/", "task": "read the page"})
    assert out.startswith("Blocked")
    assert fetched == []


def test_non_http_scheme_blocked(engine, monkeypatch):
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)
    out = engine._exec_browser({"url": "file:///etc/passwd", "task": "read it"})
    assert "Blocked" in out


def test_fetch_error_keeps_install_message(no_chromium, monkeypatch):
    engine, _ = no_chromium
    monkeypatch.setattr(engine, "_fetch_url_bytes",
                        lambda url: (None, "Blocked: redirect to internal address", None))
    out = engine._exec_browser({"url": "https://docs.example/", "task": "read it"})
    assert "Chromium browser is not installed" in out
    assert "Static fetch fallback also failed: Blocked" in out


def test_binary_content_not_returned(no_chromium, monkeypatch):
    engine, _ = no_chromium
    monkeypatch.setattr(engine, "_fetch_url_bytes", lambda url: (b"%PDF", None, "application/pdf"))
    out = engine._exec_browser({"url": "https://docs.example/a.pdf", "task": "read it"})
    assert "not a web page" in out and "%PDF" not in out


def test_local_search_endpoint_not_browsable(no_chromium, monkeypatch):
    engine, fetched = no_chromium
    monkeypatch.setattr(engine, "_SEARCH_ALLOW_HOSTS", {"127.0.0.1:8888"})
    out = engine._exec_browser({"url": "http://127.0.0.1:8888/search", "task": "read it"})
    assert "local search endpoint" in out and fetched == []


def test_text_is_capped(no_chromium, monkeypatch):
    engine, _ = no_chromium
    big = b"<p>" + b"word " * 20000 + b"</p>"
    monkeypatch.setattr(engine, "_fetch_url_bytes", lambda url: (big, None, "text/html"))
    out = engine._exec_browser({"url": "https://docs.example/", "task": "read it"})
    assert "truncated at" in out
    assert len(out) < engine.STATIC_PAGE_MAX_CHARS + 2000


def test_missing_playwright_package_hint(no_chromium, monkeypatch):
    engine, _ = no_chromium
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)
    out = engine._exec_browser({"url": "https://docs.example/", "task": "read it"})
    assert "Playwright isn't installed" in out and "-m pip install playwright" in out


def test_chromium_present_reports_ok(engine, monkeypatch, tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("stub")
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    monkeypatch.setattr(engine, "_browser_use_available", lambda: True)
    monkeypatch.setattr(engine, "_set_browser_use_log_verbosity", lambda *a: None)
    monkeypatch.setattr(engine, "_playwright_chromium_executable", lambda: str(exe))
    monkeypatch.setattr(engine, "_browser_reuse_session", lambda: False)

    async def _agent(url, task, executable_path=None):
        return "done"

    monkeypatch.setattr(engine, "_run_browser_agent", _agent)
    engine._report_browser("chromium")
    engine._exec_browser({"url": "https://docs.example/", "task": "click it"})
    assert capabilities.get(capabilities.BROWSER).state == capabilities.OK
