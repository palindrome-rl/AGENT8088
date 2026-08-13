import pytest


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost:8080/",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://10.0.0.5/internal",
    "http://192.168.1.1/router",
    "http://[::1]/",
    "file:///etc/passwd",
    "gopher://evil/",
])
def test_blocks_internal_and_bad_schemes(engine, url):
    assert engine._ssrf_check(url) is not None  # returns an error string


@pytest.mark.parametrize("url", [
    "https://example.com/page",
    "http://93.184.216.34/",   # public IP
])
def test_allows_public(engine, url):
    assert engine._ssrf_check(url) is None


def test_http_get_mode_is_blocked(engine, monkeypatch):
    monkeypatch.setitem(engine.TOOL_SPECS, "fetch",
                        {"name": "fetch", "mode": "http_get", "url": "{url}",
                         "timeout": 5, "args": ["url"], "description": "", "keywords": set()})
    out = engine.run_tool("fetch", {"url": "http://169.254.169.254/latest/meta-data/"})
    assert "Blocked" in out


def test_page_title_tool_is_ssrf_guarded(engine):
    out = engine.run_tool("get_page_title", {"url": "http://169.254.169.254/latest/meta-data/"})

    assert "Blocked" in out


def test_allow_private_opt_out(engine, monkeypatch):
    # Environments with a trusted LAN service (e.g. SearXNG) can opt out.
    monkeypatch.setattr(engine, "SSRF_ALLOW_PRIVATE", True)
    assert engine._ssrf_check("http://192.168.1.10:8888/search?q=x") is None
