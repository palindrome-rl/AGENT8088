"""Tests for the web search provider registry, precedence, and fallback chain.

Hermetic by construction: no test reaches the network, and none depends on
whether the `ddgs` package happens to be installed in the running environment
(`_ddgs_installed` is patched wherever it matters).
"""
import urllib.error

import pytest

from agent8088 import web_search


class _Stub(web_search.WebSearchProvider):
    def __init__(self, name, available=True, results=None, error=None,
                 retryable=True):
        self._name = name
        self._available = available
        self._results = results or []
        self._error = error
        self._retryable = retryable
        self.calls = 0

    @property
    def name(self):
        return self._name

    def is_available(self, ctx):
        return self._available

    def setup_schema(self):
        return {"name": self._name, "badge": "test", "tag": "stub", "env_vars": []}

    def search(self, query, limit, ctx):
        self.calls += 1
        if self._error:
            return web_search.SearchFailure(self._error, retryable=self._retryable)
        return web_search.SearchSuccess(self._results, provider=self._name)


def _no_ddgs(monkeypatch):
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: False)


def _yes_ddgs(monkeypatch):
    monkeypatch.setattr(web_search, "_ddgs_installed", lambda: True)


# ---------------------------------------------------------------------------
# Registry precedence
# ---------------------------------------------------------------------------
def test_explicit_provider_wins_over_preference_order():
    registry = web_search.Registry([_Stub("searxng"), _Stub("ddgs")])
    chain = registry.chain({"web_search_provider": "ddgs"}, ctx=None)
    assert [p.name for p in chain] == ["ddgs"]


def test_preference_order_filters_unavailable():
    registry = web_search.Registry([
        _Stub("searxng", available=False),
        _Stub("tavily", available=True),
        _Stub("ddgs", available=True),
    ])
    chain = registry.chain({}, ctx=None)
    assert [p.name for p in chain] == ["tavily", "ddgs"]


def test_base_order_is_searxng_then_ddgs_without_any_key():
    """No key configured: the two keyless backends serve, searxng first."""
    registry = web_search.Registry([
        _Stub("ddgs"), _Stub("exa", available=False),
        _Stub("tavily", available=False), _Stub("searxng"),
    ])
    assert [p.name for p in registry.chain({}, ctx=None)] == ["searxng", "ddgs"]


def test_configured_tavily_is_promoted_ahead_of_the_keyless_backends():
    """Adding a key is the signal to prefer that backend — it jumps the queue
    rather than waiting behind searxng and ddgs."""
    registry = web_search.Registry([
        _Stub("ddgs"), _Stub("exa", available=False),
        _Stub("tavily", available=True), _Stub("searxng"),
    ])
    assert [p.name for p in registry.chain({}, ctx=None)] == [
        "tavily", "searxng", "ddgs"]


def test_configured_exa_is_promoted_ahead_of_the_keyless_backends():
    registry = web_search.Registry([
        _Stub("ddgs"), _Stub("exa", available=True),
        _Stub("tavily", available=False), _Stub("searxng"),
    ])
    assert [p.name for p in registry.chain({}, ctx=None)] == [
        "exa", "searxng", "ddgs"]


def test_tavily_outranks_exa_when_both_keys_are_configured():
    registry = web_search.Registry([_Stub(n) for n in ("ddgs", "exa", "tavily", "searxng")])
    assert [p.name for p in registry.chain({}, ctx=None)] == [
        "tavily", "exa", "searxng", "ddgs"]


def test_promotion_keeps_searxng_ahead_of_ddgs():
    """The promotion reorders the front of the chain without disturbing the
    relative order of the keyless backends behind it."""
    registry = web_search.Registry([_Stub(n) for n in ("ddgs", "tavily", "searxng")])
    names = [p.name for p in registry.chain({}, ctx=None)]
    assert names.index("searxng") < names.index("ddgs")


def test_auto_is_not_treated_as_a_pin():
    """`auto` reaching chain() means startup resolution never ran. It must fall
    back to the full chain, not to "unknown provider" — search keeps working and
    the unresolved pin denies itself the no-prompt exemption."""
    registry = web_search.Registry([_Stub("searxng"), _Stub("ddgs")])
    chain = registry.chain({"web_search_provider": web_search.AUTO}, ctx=None)
    assert [p.name for p in chain] == ["searxng", "ddgs"]


# ---------------------------------------------------------------------------
# startup_pick — one backend, and SearXNG must actually answer
# ---------------------------------------------------------------------------
def test_startup_pick_requires_searxng_to_answer():
    registry = web_search.Registry([_Stub("searxng"), _Stub("ddgs")])
    assert registry.startup_pick(ctx=None, probe=lambda ctx: True) == "searxng"
    assert registry.startup_pick(ctx=None, probe=lambda ctx: False) == "ddgs"


def test_startup_pick_prefers_a_keyed_backend_and_skips_the_probe():
    registry = web_search.Registry([
        _Stub("searxng"), _Stub("ddgs"), _Stub("tavily", available=True)])
    probed = []
    picked = registry.startup_pick(ctx=None, probe=lambda ctx: probed.append(1) or True)
    assert picked == "tavily" and probed == []


def test_startup_pick_returns_empty_when_nothing_can_serve():
    registry = web_search.Registry([
        _Stub("searxng"), _Stub("ddgs", available=False)])
    assert registry.startup_pick(ctx=None, probe=lambda ctx: False) == ""


def test_probe_reports_false_without_a_base_url():
    ctx = web_search.SearchContext(config={})
    assert web_search.probe_searxng(ctx) is False


def test_probe_respects_the_egress_guard_and_makes_no_request(monkeypatch):
    """The probe is a real outbound request — it must not be the one that skips
    the guard."""
    called = []
    monkeypatch.setattr(web_search, "_http_get_json",
                        lambda *a, **k: called.append(1) or {})
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="},
        check_url=lambda url: "Blocked: egress policy")
    assert web_search.probe_searxng(ctx) is False
    assert called == []


def test_probe_reports_true_on_a_json_answer(monkeypatch):
    monkeypatch.setattr(web_search, "_http_get_json", lambda *a, **k: {"results": []})
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    assert web_search.probe_searxng(ctx) is True


def test_probe_reports_false_when_the_instance_errors(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(web_search, "_http_get_json", _boom)
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    assert web_search.probe_searxng(ctx) is False


def test_chain_is_empty_when_nothing_available():
    registry = web_search.Registry([_Stub("searxng", available=False)])
    assert registry.chain({}, ctx=None) == []


def test_unknown_explicit_provider_yields_empty_chain():
    registry = web_search.Registry([_Stub("searxng")])
    assert registry.chain({"web_search_provider": "nope"}, ctx=None) == []


# ---------------------------------------------------------------------------
# run_search fallback chain
# ---------------------------------------------------------------------------
def test_run_search_falls_through_to_next_provider():
    broken = _Stub("searxng", error="instance unreachable")
    working = _Stub("ddgs", results=[web_search.SearchResult("T", "https://e.com", "s")])
    registry = web_search.Registry([broken, working])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert broken.calls == 1 and working.calls == 1
    assert "ddgs" in out and "https://e.com" in out


def test_run_search_reports_every_failure_when_all_fail():
    registry = web_search.Registry([
        _Stub("searxng", error="unreachable"),
        _Stub("ddgs", error="rate limited"),
    ])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert "unreachable" in out and "rate limited" in out


def test_run_search_with_no_providers_names_setup_command():
    registry = web_search.Registry([_Stub("searxng", available=False)])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert "/search setup" in out


def test_run_search_stops_at_non_retryable_failure():
    blocked = _Stub("searxng", error="Blocked: egress", retryable=False)
    nxt = _Stub("ddgs", results=[web_search.SearchResult("T", "https://e.com")])
    registry = web_search.Registry([blocked, nxt])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert "Blocked: egress" in out and nxt.calls == 0


def test_run_search_rejects_empty_query():
    registry = web_search.Registry([_Stub("ddgs")])
    out = web_search.run_search("   ", 5, registry, {}, web_search.SearchContext())
    assert "query" in out.lower()


def test_run_search_names_an_unknown_pinned_provider():
    registry = web_search.Registry([_Stub("searxng")])
    out = web_search.run_search("q", 5, registry, {"web_search_provider": "bing"},
                                web_search.SearchContext())
    assert "bing" in out


def test_run_search_treats_empty_results_as_a_miss_and_continues():
    empty = _Stub("searxng", results=[])
    nxt = _Stub("ddgs", results=[web_search.SearchResult("T", "https://e.com")])
    registry = web_search.Registry([empty, nxt])
    out = web_search.run_search("q", 5, registry, {}, web_search.SearchContext())
    assert nxt.calls == 1 and "https://e.com" in out


def test_format_results_names_the_serving_provider():
    """A silent fallback would hide a broken primary — the provider is always named."""
    ctx = web_search.SearchContext()
    out = web_search.format_results(
        web_search.SearchSuccess([web_search.SearchResult("T", "https://e.com", "s")],
                                 provider="ddgs"), ctx)
    assert "via ddgs" in out


# ---------------------------------------------------------------------------
# SearXNG provider
# ---------------------------------------------------------------------------
def test_searxng_unavailable_without_base_url():
    p = web_search.SearxngProvider()
    assert p.is_available(web_search.SearchContext(config={})) is False


def test_searxng_available_with_base_url():
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    assert web_search.SearxngProvider().is_available(ctx) is True


def test_searxng_returns_guard_error_verbatim_and_non_retryable():
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://10.0.0.5:8888/search?q="},
        check_url=lambda url: "Blocked: private address",
    )
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure)
    assert out.error == "Blocked: private address" and out.retryable is False


def test_searxng_rejects_plaintext_http_to_public_host():
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://searx.example.com/search?q="})
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure)
    assert "https" in out.error.lower() and out.retryable is False


def test_searxng_allows_plaintext_http_to_loopback(monkeypatch):
    monkeypatch.setattr(web_search, "_http_get_json", lambda url, timeout: {"results": []})
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchSuccess)


def test_searxng_parses_and_ranks_results(monkeypatch):
    payload = {"results": [
        {"title": "A", "url": "https://a.com", "content": "sa", "score": 1.0},
        {"title": "B", "url": "https://b.com", "content": "sb", "score": 9.0},
    ]}
    monkeypatch.setattr(web_search, "_http_get_json", lambda url, timeout: payload)
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    out = web_search.SearxngProvider().search("q", 1, ctx)
    assert isinstance(out, web_search.SearchSuccess)
    assert [r.title for r in out.results] == ["B"]


def test_searxng_html_response_names_the_json_format_setting(monkeypatch):
    def _raise(url, timeout):
        raise ValueError("not json")

    monkeypatch.setattr(web_search, "_http_get_json", _raise)
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert "formats" in out.error and "json" in out.error


def test_searxng_403_names_the_limiter(monkeypatch):
    def _raise(url, timeout):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(web_search, "_http_get_json", _raise)
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert "limiter" in out.error


def test_searxng_unreachable_is_retryable(monkeypatch):
    def _raise(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(web_search, "_http_get_json", _raise)
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    out = web_search.SearxngProvider().search("q", 5, ctx)
    assert out.retryable is True and "reach" in out.error.lower()


def test_searxng_url_encodes_the_query(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_search, "_http_get_json",
                        lambda url, timeout: seen.setdefault("url", url) and None
                        or {"results": []})
    ctx = web_search.SearchContext(
        config={"search_base_url": "http://127.0.0.1:8888/search?q="})
    web_search.SearxngProvider().search("a b&c", 5, ctx)
    assert "a%20b%26c" in seen["url"] and "format=json" in seen["url"]


# ---------------------------------------------------------------------------
# ddgs provider — the keyless bundled fallback
# ---------------------------------------------------------------------------
def test_ddgs_unavailable_when_package_missing(monkeypatch):
    _no_ddgs(monkeypatch)
    assert web_search.DdgsProvider().is_available(web_search.SearchContext()) is False


def test_ddgs_advertises_itself_as_bundled():
    schema = web_search.DdgsProvider().setup_schema()
    assert "bundled" in schema["badge"] and schema["env_vars"] == []


def test_chain_always_has_a_fallback_with_nothing_configured(monkeypatch):
    """The payoff of bundling ddgs: an empty config still yields a usable chain."""
    _yes_ddgs(monkeypatch)
    registry = web_search.default_registry()
    ctx = web_search.SearchContext(config={}, get_secret=lambda n: "")
    assert [p.name for p in registry.chain({}, ctx)] == ["ddgs"]


def test_ddgs_serves_when_searxng_is_configured_but_broken(monkeypatch):
    """The exact scenario that motivated bundling: SearXNG set but not working."""
    _yes_ddgs(monkeypatch)
    monkeypatch.setattr(web_search, "_ddgs_text", lambda q, n: [
        {"title": "T", "href": "https://e.com", "body": "b"}])

    def _refused(url, timeout):
        raise OSError("refused")

    monkeypatch.setattr(web_search, "_http_get_json", _refused)
    config = {"search_base_url": "http://127.0.0.1:8888/search?q="}
    ctx = web_search.SearchContext(config=config, get_secret=lambda n: "")
    out = web_search.run_search("q", 5, web_search.default_registry(), config, ctx)
    assert "ddgs" in out and "https://e.com" in out


def test_ddgs_fails_closed_and_never_calls_library_when_egress_blocks(monkeypatch):
    """D8 requirement: the library must not run at all under a blocking policy.

    Assert BOTH the non-retryable failure and that the library was never
    invoked — that is what makes the pre-flight meaningful rather than
    decorative.
    """
    called = []
    _yes_ddgs(monkeypatch)
    monkeypatch.setattr(web_search, "_ddgs_text", lambda q, n: called.append(q) or [])
    ctx = web_search.SearchContext(check_url=lambda url: "Blocked: not in allowed_domains")
    out = web_search.DdgsProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure)
    assert out.retryable is False and called == []


def test_ddgs_checks_every_upstream_host(monkeypatch):
    """A partial check would leave an unguarded host reachable."""
    checked = []
    _yes_ddgs(monkeypatch)
    monkeypatch.setattr(web_search, "_ddgs_text", lambda q, n: [])
    ctx = web_search.SearchContext(check_url=lambda url: checked.append(url))
    web_search.DdgsProvider().search("q", 5, ctx)
    assert set(checked) == set(web_search._DDGS_HOSTS)


def test_ddgs_maps_library_result_keys(monkeypatch):
    _yes_ddgs(monkeypatch)
    monkeypatch.setattr(web_search, "_ddgs_text", lambda q, n: [
        {"title": "T", "href": "https://e.com", "body": "snippet"}])
    out = web_search.DdgsProvider().search("q", 5, web_search.SearchContext())
    assert isinstance(out, web_search.SearchSuccess)
    assert out.results[0].url == "https://e.com" and out.results[0].snippet == "snippet"


def test_ddgs_rate_limit_is_retryable(monkeypatch):
    _yes_ddgs(monkeypatch)

    def _boom(q, n):
        raise RuntimeError("202 Ratelimit")

    monkeypatch.setattr(web_search, "_ddgs_text", _boom)
    out = web_search.DdgsProvider().search("q", 5, web_search.SearchContext())
    assert isinstance(out, web_search.SearchFailure)
    assert "rate" in out.error.lower() and out.retryable is True


def test_ddgs_missing_package_at_call_time_is_reported(monkeypatch):
    _no_ddgs(monkeypatch)
    out = web_search.DdgsProvider().search("q", 5, web_search.SearchContext())
    assert isinstance(out, web_search.SearchFailure) and "ddgs" in out.error


# ---------------------------------------------------------------------------
# Optional API-key providers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cls,env", [
    (web_search.TavilyProvider, "TAVILY_API_KEY"),
    (web_search.ExaProvider, "EXA_API_KEY"),
])
def test_keyed_provider_availability_follows_secret(cls, env):
    empty = web_search.SearchContext(get_secret=lambda n: "")
    assert cls().is_available(empty) is False
    present = web_search.SearchContext(get_secret=lambda n: "k" if n == env else "")
    assert cls().is_available(present) is True


@pytest.mark.parametrize("cls,env", [
    (web_search.TavilyProvider, "TAVILY_API_KEY"),
    (web_search.ExaProvider, "EXA_API_KEY"),
])
def test_keyed_provider_setup_schema_names_env_var(cls, env):
    schema = cls().setup_schema()
    assert schema["env_vars"][0]["key"] == env
    assert "optional" in schema["badge"]


def test_optional_providers_absent_from_chain_without_keys(monkeypatch):
    """A user who never added a key must not see tavily/exa in the chain."""
    _yes_ddgs(monkeypatch)
    registry = web_search.default_registry()
    ctx = web_search.SearchContext(config={}, get_secret=lambda n: "")
    names = [p.name for p in registry.chain({}, ctx)]
    assert "tavily" not in names and "exa" not in names


def test_optional_provider_enters_chain_once_key_is_set(monkeypatch):
    _yes_ddgs(monkeypatch)
    registry = web_search.default_registry()
    ctx = web_search.SearchContext(
        config={}, get_secret=lambda n: "k" if n == "TAVILY_API_KEY" else "")
    assert [p.name for p in registry.chain({}, ctx)] == ["tavily", "ddgs"]


# ---------------------------------------------------------------------------
# End-to-end ordering with the real providers, exercising the same
# is_available() paths a live install uses rather than stub flags.
# ---------------------------------------------------------------------------
_SEARXNG_CONFIG = {"search_base_url": "http://127.0.0.1:8888/search?q="}


def _real_chain(monkeypatch, keys=()):
    _yes_ddgs(monkeypatch)
    registry = web_search.default_registry()
    ctx = web_search.SearchContext(
        config=_SEARXNG_CONFIG, get_secret=lambda n: "k" if n in keys else "")
    return [p.name for p in registry.chain({}, ctx)]


def test_real_chain_is_searxng_then_ddgs_with_no_keys(monkeypatch):
    """The configuration this repo ships as the default: a self-hosted SearXNG
    serves, ddgs backs it up, and neither optional vendor is consulted."""
    assert _real_chain(monkeypatch) == ["searxng", "ddgs"]


def test_real_chain_promotes_tavily_when_its_key_is_added(monkeypatch):
    assert _real_chain(monkeypatch, keys=("TAVILY_API_KEY",)) == [
        "tavily", "searxng", "ddgs"]


def test_real_chain_promotes_exa_when_its_key_is_added(monkeypatch):
    assert _real_chain(monkeypatch, keys=("EXA_API_KEY",)) == [
        "exa", "searxng", "ddgs"]


def test_real_chain_puts_tavily_before_exa_when_both_keys_are_added(monkeypatch):
    assert _real_chain(monkeypatch, keys=("TAVILY_API_KEY", "EXA_API_KEY")) == [
        "tavily", "exa", "searxng", "ddgs"]


def test_tavily_parses_results(monkeypatch):
    monkeypatch.setattr(web_search, "_http_json", lambda **kw: {
        "results": [{"title": "T", "url": "https://e.com", "content": "c"}]})
    ctx = web_search.SearchContext(get_secret=lambda n: "key")
    out = web_search.TavilyProvider().search("q", 5, ctx)
    assert out.results[0].url == "https://e.com"


def test_exa_parses_results(monkeypatch):
    monkeypatch.setattr(web_search, "_http_json", lambda **kw: {
        "results": [{"title": "T", "url": "https://e.com", "text": "t"}]})
    ctx = web_search.SearchContext(get_secret=lambda n: "key")
    out = web_search.ExaProvider().search("q", 5, ctx)
    assert out.results[0].snippet == "t"


def test_keyed_provider_sends_only_its_own_key(monkeypatch):
    """Tavily's key must never appear in an Exa request."""
    seen = {}
    monkeypatch.setattr(web_search, "_http_json",
                        lambda **kw: seen.update(kw) or {"results": []})
    ctx = web_search.SearchContext(
        get_secret=lambda n: {"TAVILY_API_KEY": "tav", "EXA_API_KEY": "exa"}.get(n, ""))
    web_search.ExaProvider().search("q", 5, ctx)
    assert "tav" not in str(seen) and "exa" in str(seen)


def test_keyed_provider_honors_guard():
    ctx = web_search.SearchContext(get_secret=lambda n: "key",
                                   check_url=lambda url: "Blocked: blocked_domains")
    out = web_search.ExaProvider().search("q", 5, ctx)
    assert isinstance(out, web_search.SearchFailure) and out.retryable is False


def test_keyed_provider_401_is_not_retryable(monkeypatch):
    def _boom(**kw):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(web_search, "_http_json", _boom)
    ctx = web_search.SearchContext(get_secret=lambda n: "bad")
    out = web_search.TavilyProvider().search("q", 5, ctx)
    assert out.retryable is False and "TAVILY_API_KEY" in out.error


def test_keyed_provider_429_is_retryable(monkeypatch):
    def _boom(**kw):
        raise urllib.error.HTTPError("u", 429, "Too Many", {}, None)

    monkeypatch.setattr(web_search, "_http_json", _boom)
    ctx = web_search.SearchContext(get_secret=lambda n: "k")
    out = web_search.TavilyProvider().search("q", 5, ctx)
    assert out.retryable is True


def test_default_registry_exposes_all_four_backends():
    assert set(web_search.default_registry().names()) == set(web_search.PREFERENCE)
