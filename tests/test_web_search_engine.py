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


def test_packaged_config_enables_the_temporary_lan_search_profile(engine):
    config = engine.load_simple_config(engine.APP_DIR / "config.txt")
    assert config["search_base_url"] == "http://127.0.0.1:8888/search?q="
    # `auto` resolves to a searxng PIN whenever the LAN instance answers, which
    # is what keeps web_search_no_prompt in force (see
    # test_auto_resolution_keeps_the_no_prompt_exemption_for_local_searxng).
    assert config["web_search_provider"] == "auto"
    assert config["web_search_no_prompt"] == "1"
    assert "127.0.0.1:8888" in config["ssrf_allow_hosts"]


# ---------------------------------------------------------------------------
# web_search_provider=auto — startup resolution
#
# No test here touches the network: every probe is injected. The LAN profile's
# real instance must never be contacted by the suite.
# ---------------------------------------------------------------------------
def _auto_engine(engine, *, base_url="http://127.0.0.1:8888/search?q=",
                 keys=(), no_prompt="1"):
    engine.APP_CONFIG["web_search_provider"] = "auto"
    engine.APP_CONFIG["web_search_no_prompt"] = no_prompt
    engine.APP_CONFIG["search_base_url"] = base_url
    engine.SEARCH_BASE_URL_CONFIGURED = bool(base_url)
    engine._search_context = lambda: engine.web_search.SearchContext(
        config=engine._search_config(),
        get_secret=lambda n: "k" if n in keys else "",
        check_url=lambda url: None,
        wrap=lambda text, source="": text)
    return engine


def test_auto_picks_searxng_when_the_instance_answers(engine, monkeypatch):
    _auto_engine(engine)
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    assert engine.resolve_auto_search_provider(probe=lambda ctx: True) == "searxng"
    assert engine.APP_CONFIG["web_search_provider"] == "searxng"


def test_auto_falls_to_ddgs_when_searxng_does_not_answer(engine, monkeypatch):
    """The whole point of probing: a pinned dead instance would be no search."""
    _auto_engine(engine)
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    assert engine.resolve_auto_search_provider(probe=lambda ctx: False) == "ddgs"


def test_auto_prefers_a_keyed_backend_without_probing_searxng(engine, monkeypatch):
    """A tavily key outranks SearXNG, so the probe must not even run."""
    _auto_engine(engine, keys=("TAVILY_API_KEY",))
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    probed = []
    resolved = engine.resolve_auto_search_provider(
        probe=lambda ctx: probed.append(1) or True)
    assert resolved == "tavily"
    assert probed == []


def test_auto_resolution_keeps_the_no_prompt_exemption_for_local_searxng(engine):
    """The reason auto pins instead of staying dynamic: silent local search."""
    _auto_engine(engine)
    engine.APP_CONFIG["ssrf_allow_hosts"] = "127.0.0.1,localhost"
    assert engine.resolve_auto_search_provider(probe=lambda ctx: True) == "searxng"
    assert engine._local_searxng_no_prompt_enabled() is True


def test_ddgs_resolution_does_not_get_the_no_prompt_exemption(engine, monkeypatch):
    """Silent + external is the combination auto must never produce: a ddgs
    query leaves the network, so it has to be approved per search."""
    _auto_engine(engine)
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    assert engine.resolve_auto_search_provider(probe=lambda ctx: False) == "ddgs"
    assert engine._local_searxng_no_prompt_enabled() is False


def test_auto_is_a_noop_when_a_backend_is_explicitly_pinned(engine):
    """An operator's own pin is never overridden by startup probing."""
    engine.APP_CONFIG["web_search_provider"] = "ddgs"
    probed = []
    resolved = engine.resolve_auto_search_provider(
        probe=lambda ctx: probed.append(1) or True)
    assert resolved == "ddgs" and probed == []
    assert engine.APP_CONFIG["web_search_provider"] == "ddgs"


def test_resolving_twice_does_not_probe_again(engine, monkeypatch):
    _auto_engine(engine)
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: True)
    calls = []
    engine.resolve_auto_search_provider(probe=lambda ctx: calls.append(1) or True)
    engine.resolve_auto_search_provider(probe=lambda ctx: calls.append(1) or True)
    assert len(calls) == 1


def test_auto_reports_empty_when_nothing_can_serve(engine, monkeypatch):
    _auto_engine(engine, base_url="")
    monkeypatch.setattr(engine.web_search, "_ddgs_installed", lambda: False)
    assert engine.resolve_auto_search_provider(probe=lambda ctx: False) == ""


def test_unresolved_auto_never_wins_the_no_prompt_exemption(engine):
    """Fail-safe: an embedder that skips startup resolution must not get silent
    searches just because the config says auto."""
    _auto_engine(engine)
    assert engine.APP_CONFIG["web_search_provider"] == "auto"
    assert engine._local_searxng_no_prompt_enabled() is False


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
    # Results carry a retrieval-date stamp, so assert the search ran and its
    # output came back — not that the string is byte-identical.
    assert "OK" in engine.run_tool("web_search", {"query": "weather"})


def _enable_local_searxng_without_prompt(engine):
    engine.SEARCH_BASE_URL_CONFIGURED = True
    engine.APP_CONFIG.update({
        "search_base_url": "http://127.0.0.1:8888/search?q=",
        "web_search_provider": "searxng",
        "web_search_no_prompt": "1",
    })


def test_search_requires_permission_in_readonly_without_local_opt_in(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    out = engine.run_tool("web_search", {"query": "hi"})
    assert "ESCALATION" in out.upper()


def test_local_searxng_search_runs_without_permission_in_readonly(engine, monkeypatch):
    _enable_local_searxng_without_prompt(engine)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine.web_search, "run_search", lambda *a, **k: "OK")

    result = engine.run_tool("web_search", {"query": "hi"})

    assert "OK" in result
    assert not result.startswith("ESCALATION_REQUEST\x1f")


def test_local_searxng_search_runs_without_permission_in_plan_only(engine, monkeypatch):
    _enable_local_searxng_without_prompt(engine)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(engine.web_search, "run_search", lambda *a, **k: "OK")

    result = engine.run_tool("web_search", {"query": "hi"})

    assert "OK" in result
    assert not result.startswith("ESCALATION_REQUEST\x1f")


def test_allowlisted_private_lan_searxng_runs_without_permission(engine, monkeypatch):
    engine.SEARCH_BASE_URL_CONFIGURED = True
    engine.APP_CONFIG.update({
        "search_base_url": "http://127.0.0.1:8888/search?q=",
        "web_search_provider": "searxng",
        "web_search_no_prompt": "1",
        "ssrf_allow_hosts": "127.0.0.1,localhost,127.0.0.1:8888",
    })
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine.web_search, "run_search", lambda *a, **k: "OK")
    assert engine._local_searxng_no_prompt_enabled() is True

    result = engine.run_tool("web_search", {"query": "Pakistan public holidays"})

    assert "OK" in result
    assert not result.startswith("ESCALATION_REQUEST\x1f")


def test_no_prompt_search_cannot_use_a_nonlocal_or_unpinned_provider(engine):
    engine.SEARCH_BASE_URL_CONFIGURED = True
    engine.APP_CONFIG.update({
        "search_base_url": "https://search.example.com/search?q=",
        "web_search_provider": "searxng",
        "web_search_no_prompt": "1",
    })
    assert engine._local_searxng_no_prompt_enabled() is False
    engine.APP_CONFIG["search_base_url"] = "http://127.0.0.1:8888/search?q="
    engine.APP_CONFIG["web_search_provider"] = "ddgs"
    assert engine._local_searxng_no_prompt_enabled() is False


def test_search_blocks_sensitive_query_data_before_calling_provider(engine, monkeypatch):
    _enable_local_searxng_without_prompt(engine)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine.web_search, "run_search",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    for query in ("alice@example.com", "password=correct-horse-battery-staple",
                  "AKIAIOSFODNN7EXAMPLE", "555-555-1234"):
        assert "Blocked" in engine.run_tool("web_search", {"query": query})


def test_search_allows_a_non_sensitive_password_topic(engine):
    assert engine._web_search_query_guard("how to reset a password") is None


def test_system_prompt_directs_proactive_web_search(engine):
    assert "Proactively call web_search" in engine.BASE_SYSTEM_PROMPT
    assert "current leaders or roles" in engine.BASE_SYSTEM_PROMPT
    assert "Never use execute_shell for web research" in engine.BASE_SYSTEM_PROMPT


def test_tool_descriptions_prefer_search_over_browser_or_shell(engine):
    assert "Always use it before answering about current leaders" in (
        engine.TOOL_SPECS["web_search"]["description"])
    assert "user-supplied web page" in (
        engine.TOOL_SPECS["browse_page"]["description"])
    assert "Never use it for web research" in (
        engine.TOOL_SPECS["execute_shell"]["description"])


def test_search_results_block_unsolicited_browser_followup(engine, monkeypatch):
    from tests.conftest import ScriptedModel

    browser_runs = []
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **_: (
        browser_runs.append((name, args)) if name == "browse_page" else "search results"
    ))
    engine.create_completion = ScriptedModel([
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "next F1 race"}',
        '✿FUNCTION✿: browse_page ✿ARGS✿: {"url": "https://example.com/race"}',
        "The search results answer the question.",
    ])

    assert engine.run_agent([{"role": "user", "content": "When is the next Formula 1 race?"}]) == (
        "The search results answer the question.")
    assert browser_runs == []


def test_search_allows_browser_for_user_supplied_url(engine, monkeypatch):
    from tests.conftest import ScriptedModel

    browser_runs = []
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **_: (
        browser_runs.append((name, args)) or "page loaded" if name == "browse_page" else "search results"
    ))
    url = "https://example.com/race"
    engine.create_completion = ScriptedModel([
        '✿FUNCTION✿: web_search ✿ARGS✿: {"query": "next F1 race"}',
        f'✿FUNCTION✿: browse_page ✿ARGS✿: {{"url": "{url}"}}',
        "The page confirms it.",
    ])

    assert engine.run_agent([{"role": "user", "content": f"Check {url}"}]) == "The page confirms it."
    assert browser_runs == [("browse_page", {"url": url})]


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
