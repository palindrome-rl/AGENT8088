"""Tests for the http_get/http_post modes, jq filters, and the SSRF allowlist."""


def test_safe_format_survives_json_braces(engine):
    # str.format() raises KeyError '"query"' on this; _safe_format must not.
    tpl = '{"query": "{query}", "max_results": 6, "nested": {"a": 1}}'
    out = engine._safe_format(tpl, {"query": "hello world"})
    assert out == '{"query": "hello world", "max_results": 6, "nested": {"a": 1}}'


def test_safe_format_leaves_unknown_placeholders(engine):
    assert engine._safe_format("Bearer {nope_key}", {}) == "Bearer {nope_key}"


def test_safe_format_supports_quoted_variant(engine):
    assert engine._safe_format("q={query_q}", {"query": "a b&c"}) == "q=a%20b%26c"


def test_http_get_applies_jq_filter(engine, monkeypatch):
    seen = {}

    def fake_shell(cmd, timeout=25):
        seen["cmd"] = cmd
        return "filtered"

    monkeypatch.setattr(engine, "_exec_shell_command", fake_shell)
    spec = {"name": "t", "mode": "http_get", "url": "https://example.com/api",
            "filter": ".results[]", "headers": "", "body": ""}
    out = engine._exec_http("http_get", spec, {}, 10)
    assert out == "filtered"
    assert "jq -r" in seen["cmd"]
    assert "curl -s" in seen["cmd"]


def test_http_post_sends_body_and_headers(engine, monkeypatch):
    seen = {}

    def fake_shell(cmd, timeout=25):
        seen["cmd"] = cmd
        return "ok"

    monkeypatch.setattr(engine, "_exec_shell_command", fake_shell)
    spec = {"name": "t", "mode": "http_post", "url": "https://example.com/s",
            "headers": "Content-Type: application/json;;X-Key: abc",
            "body": '{"q": "{query}"}', "filter": ""}
    engine._exec_http("http_post", spec, {"query": "hi"}, 10)
    cmd = seen["cmd"]
    assert "-X POST" in cmd
    assert "Content-Type: application/json" in cmd
    assert "X-Key: abc" in cmd
    assert '"q": "hi"' in cmd


def test_http_reports_unconfigured_api_key(engine):
    spec = {"name": "web_search_tavily", "mode": "http_post",
            "url": "https://api.tavily.com/search",
            "headers": "Authorization: Bearer {tavily_api_key}",
            "body": "{}", "filter": ""}
    out = engine._exec_http("http_post", spec, {"query": "x"}, 10)
    assert "not configured" in out
    assert "tavily_api_key" in out


def test_http_enforces_ssrf(engine, monkeypatch):
    monkeypatch.setattr(engine, "SSRF_ALLOW_PRIVATE", False)
    monkeypatch.setattr(engine, "SSRF_ALLOW_HOSTS", set())
    spec = {"name": "t", "mode": "http_get", "url": "http://127.0.0.1/admin",
            "headers": "", "body": "", "filter": ""}
    assert "Blocked" in engine._exec_http("http_get", spec, {}, 10)


def test_ssrf_host_allowlist_permits_only_listed_host(engine, monkeypatch):
    monkeypatch.setattr(engine, "SSRF_ALLOW_PRIVATE", False)
    monkeypatch.setattr(engine, "SSRF_ALLOW_HOSTS", {"192.168.1.10"})
    assert engine._ssrf_check("http://192.168.1.10:8888/search?q=x") is None
    # A different host on the same private network stays blocked.
    assert engine._ssrf_check("http://192.168.1.11/admin") is not None
    assert engine._ssrf_check("http://169.254.169.254/") is not None


def test_ssrf_allowlist_supports_host_port(engine, monkeypatch):
    monkeypatch.setattr(engine, "SSRF_ALLOW_PRIVATE", False)
    monkeypatch.setattr(engine, "SSRF_ALLOW_HOSTS", {"10.0.0.5:9200"})
    assert engine._ssrf_check("http://10.0.0.5:9200/_search") is None
    assert engine._ssrf_check("http://10.0.0.5:22/") is not None


def test_search_tools_declared(engine):
    for name in ("web_search", "web_search_tavily", "web_search_exa"):
        assert name in engine.TOOL_NAMES
    assert engine.TOOL_SPECS["web_search"]["mode"] == "http_get"
    assert engine.TOOL_SPECS["web_search_tavily"]["mode"] == "http_post"


def test_config_defaults_are_visible_to_templates(engine):
    # Regression: tool URL templates interpolate from APP_CONFIG, so engine
    # defaults must be seeded there. Otherwise {search_base_url} stayed literal
    # and web_search failed with "Blocked: scheme '' is not allowed".
    assert "search_base_url" in engine.APP_CONFIG
    url = engine._safe_format(engine.TOOL_SPECS["web_search"]["url"], {"query": "x"})
    assert url.startswith(("http://", "https://")), url
    assert "{" not in url, f"unresolved placeholder in {url}"


def test_default_search_base_url_has_no_trailing_placeholder(engine):
    # tools.txt appends {query_q}; a trailing {query} in the base would double it.
    assert "{query}" not in engine.SEARCH_BASE_URL


def test_unresolved_placeholder_names_the_missing_arg(engine):
    spec = {"name": "web_search", "mode": "http_get", "args": ["query"],
            "url": "http://example.com/s?q={query_q}",
            "headers": "", "body": "", "filter": ""}
    out = engine._exec_http("http_get", spec, {}, 10)
    assert "unresolved placeholder" in out
    assert "pass query=" in out          # not "query_q="
    assert "scheme" not in out           # not the confusing SSRF message


def test_unresolved_config_key_points_at_config_file(engine):
    spec = {"name": "t", "mode": "http_get", "args": ["query"],
            "url": "{some_missing_base}search?q={query_q}",
            "headers": "", "body": "", "filter": ""}
    out = engine._exec_http("http_get", spec, {"query": "x"}, 10)
    assert "some_missing_base" in out
    assert "config" in out.lower()
