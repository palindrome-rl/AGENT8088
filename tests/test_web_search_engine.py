"""Engine-side wiring for mode=search: tool surface, guard injection, gating.

The registry itself is covered by tests/test_web_search.py. These tests assert
that engine.py hands it the right config and the right guards, and that the
permission layer treats a search like the network action it is.
"""


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------
def test_web_search_declares_search_mode(engine):
    assert engine.TOOL_SPECS["web_search"]["mode"] == "search"
    assert engine.TOOL_SPECS["web_search"]["args"] == ["query"]


def test_legacy_provider_tools_are_gone(engine):
    """Tavily/Exa remain as BACKENDS; only the separate tool names go away."""
    assert "web_search_tavily" not in engine.TOOL_SPECS
    assert "web_search_exa" not in engine.TOOL_SPECS


def test_web_search_is_the_only_search_tool(engine):
    search_tools = [n for n, s in engine.TOOL_SPECS.items()
                    if s.get("mode") == "search"]
    assert search_tools == ["web_search"]


# ---------------------------------------------------------------------------
# Guard injection
# ---------------------------------------------------------------------------
def test_search_context_guard_chains_egress_then_ssrf(engine, monkeypatch):
    """Egress is a string check; SSRF resolves DNS. Order matters — resolving a
    host the policy already rejected would leak the attempt to its nameserver."""
    calls = []
    monkeypatch.setattr(engine, "_egress_check", lambda url: calls.append("egress") or None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: calls.append("ssrf") or None)
    monkeypatch.setattr(engine, "_outbound_secret_check", lambda text: calls.append("secret") or None)
    ctx = engine._search_context()
    assert ctx.check_url("https://example.com") is None
    assert calls == ["egress", "ssrf", "secret"]


def test_search_context_guard_short_circuits_on_egress_denial(engine, monkeypatch):
    monkeypatch.setattr(engine, "_egress_check", lambda url: "Blocked: allowed_domains")
    monkeypatch.setattr(engine, "_ssrf_check",
                        lambda url: (_ for _ in ()).throw(AssertionError("must not run")))
    assert "allowed_domains" in engine._search_context().check_url("https://e.com")


def test_search_context_guard_blocks_outbound_secret(engine, monkeypatch):
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_outbound_secret_check",
                        lambda text: "Blocked: credential in URL")
    assert "credential" in engine._search_context().check_url("https://e.com?k=secret")


def test_search_context_reads_keys_from_the_env_store(engine, tmp_path):
    """Credentials come from the .env store, never config.txt."""
    env_file = tmp_path / ".env"
    env_file.write_text("TAVILY_API_KEY=tvly-abc\n# comment\n")
    engine.ENV_FILE_PATH = env_file
    ctx = engine._search_context()
    assert ctx.get_secret("TAVILY_API_KEY") == "tvly-abc"
    assert ctx.get_secret("EXA_API_KEY") == ""


def test_search_context_falls_back_to_process_env(engine, tmp_path, monkeypatch):
    engine.ENV_FILE_PATH = tmp_path / "missing.env"
    monkeypatch.setenv("EXA_API_KEY", "from-environ")
    assert engine._search_context().get_secret("EXA_API_KEY") == "from-environ"


def test_search_context_survives_an_unreadable_env_file(engine, tmp_path):
    engine.ENV_FILE_PATH = tmp_path  # a directory, not a file
    assert engine._search_context().get_secret("TAVILY_API_KEY") == ""


# ---------------------------------------------------------------------------
# Defaulted vs configured search_base_url
# ---------------------------------------------------------------------------
def test_search_config_strips_a_defaulted_base_url(engine):
    """The default exists so tool templates interpolate; it is not user intent.

    Treating it as configured would make SearXNG claim availability on every
    machine and shadow the keyless fallback.
    """
    engine.SEARCH_BASE_URL_CONFIGURED = False
    assert "search_base_url" not in engine._search_config()


def test_search_config_keeps_a_configured_base_url(engine):
    engine.SEARCH_BASE_URL_CONFIGURED = True
    engine.APP_CONFIG["search_base_url"] = "http://127.0.0.1:8888/search?q="
    assert engine._search_config()["search_base_url"].startswith("http://127.0.0.1")


def test_chain_never_empty_thanks_to_bundled_ddgs(engine, monkeypatch):
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    engine.SEARCH_BASE_URL_CONFIGURED = False
    assert engine._search_chain_summary() == "ddgs"


def test_chain_puts_searxng_ahead_of_the_fallback(engine, monkeypatch):
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    engine.SEARCH_BASE_URL_CONFIGURED = True
    engine.APP_CONFIG["search_base_url"] = "http://127.0.0.1:8888/search?q="
    assert engine._search_chain_summary() == "searxng -> ddgs"


# ---------------------------------------------------------------------------
# Result limit
# ---------------------------------------------------------------------------
def test_web_search_limit_defaults_to_five(engine):
    engine.APP_CONFIG.pop("web_search_results", None)
    assert engine._web_search_limit() == 5


def test_web_search_limit_is_clamped(engine):
    engine.APP_CONFIG["web_search_results"] = "500"
    assert engine._web_search_limit() == 20
    engine.APP_CONFIG["web_search_results"] = "0"
    assert engine._web_search_limit() == 1


def test_web_search_limit_survives_garbage(engine):
    engine.APP_CONFIG["web_search_results"] = "lots"
    assert engine._web_search_limit() == 5


# ---------------------------------------------------------------------------
# run_tool integration
# ---------------------------------------------------------------------------
def test_search_results_are_wrapped_untrusted(engine, monkeypatch):
    """Search results are attacker-controlled text — they must be fenced."""
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda q, limit, registry, config, ctx:
                        ctx.wrap("body", source="web_search:stub"))
    out = engine.run_tool("web_search", {"query": "hi"})
    assert "EXTERNAL_UNTRUSTED_CONTENT" in out


def test_search_passes_query_and_limit_through(engine, monkeypatch):
    seen = {}
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    engine.APP_CONFIG["web_search_results"] = "3"
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda q, limit, registry, config, ctx:
                        seen.update(query=q, limit=limit) or "ok")
    engine.run_tool("web_search", {"query": "  spaced  "})
    assert seen == {"query": "spaced", "limit": 3}


def test_search_rejects_an_empty_query_before_any_provider(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    out = engine.run_tool("web_search", {"query": "   "})
    assert "query" in out.lower()


def test_search_refuses_a_query_carrying_a_credential(engine, monkeypatch):
    """A search query is an outbound channel, so it gets the same hard floor the
    http path applies to a URL or body.

    Regression: the http_get path checked _outbound_secret_check on the URL and
    args. mode=search only guards the destination URL, and for ddgs/Tavily/Exa
    the query never appears in a URL the guard sees — so without this the query
    itself became an exfiltration path.
    """
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_SECRET_VALUES", ["sk-verysecret123"])
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no backend may see the secret")))
    out = engine.run_tool("web_search", {"query": "sk-verysecret123"})
    assert "credential" in out.lower()


def test_search_refuses_a_credential_in_any_argument(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_SECRET_VALUES", ["sk-verysecret123"])
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no backend may see the secret")))
    out = engine.run_tool("web_search", {"query": "ok", "extra": "sk-verysecret123"})
    assert "credential" in out.lower()


def test_search_secret_floor_holds_in_every_permission_mode(engine, monkeypatch):
    """Not escalatable: no mode grants sending a credential outbound."""
    monkeypatch.setattr(engine, "_SECRET_VALUES", ["sk-verysecret123"])
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no backend may see the secret")))
    for mode in ("readonly", "edit", "full-auto"):
        monkeypatch.setattr(engine, "PERMISSION_MODE", mode)
        out = engine.run_tool("web_search", {"query": "sk-verysecret123"})
        assert "credential" in out.lower(), f"not blocked in {mode}"


def test_search_allows_a_clean_query(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "_SECRET_VALUES", ["sk-verysecret123"])
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda q, limit, registry, config, ctx: "OK")
    assert engine.run_tool("web_search", {"query": "weather"}) == "OK"


def test_search_requires_permission_in_readonly(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    out = engine.run_tool("web_search", {"query": "hi"})
    assert "ESCALATION" in out.upper()


def test_search_is_denied_in_plan_only(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    out = engine.run_tool("web_search", {"query": "hi"})
    assert "ESCALATION" in out.upper() or "plan" in out.lower()


# ---------------------------------------------------------------------------
# Capabilities reporting
# ---------------------------------------------------------------------------
def test_capabilities_reports_the_live_chain(engine, monkeypatch):
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    engine.SEARCH_BASE_URL_CONFIGURED = True
    engine.APP_CONFIG["search_base_url"] = "http://127.0.0.1:8888/search?q="
    report = engine.describe_capabilities()
    assert "Web search: searxng -> ddgs" in report


def test_capabilities_reports_missing_search_backends(engine, monkeypatch):
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: False)
    engine.SEARCH_BASE_URL_CONFIGURED = False
    assert "none configured" in engine.describe_capabilities()
