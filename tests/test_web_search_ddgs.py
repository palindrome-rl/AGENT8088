"""DdgsProvider: detection, engine rotation, throttling, policy, repeat queries.

Settings follow Hermes' documented practice for this backend (DDGS as a context
manager, max_results always as a keyword, a delay BETWEEN searches) plus explicit
engine rotation, which Hermes does not specify. See
.claude/plans/ddgs-engine-hosts.md for where the host map comes from.
"""
import builtins
import inspect

import pytest

from agent8088 import web_search
from agent8088.web_search import DdgsProvider, SearchFailure, SearchSuccess


class FakeCtx:
    """Minimal SearchContext stand-in.

    blocked_hosts names substrings the egress policy denies, mirroring how
    engine.py's check_url returns a non-empty reason string on refusal.
    """

    def __init__(self, blocked_hosts=(), config=None):
        self.blocked_hosts = tuple(blocked_hosts)
        self.config = dict(config or {})

    def check_url(self, url):
        return next((f"{h} is not in ssrf_allow_hosts"
                     for h in self.blocked_hosts if h in url), "")

    def get_secret(self, _name):
        return ""

    def wrap(self, text, source=""):
        return text


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    web_search._ddgs_cache.clear()
    web_search._ddgs_import_state = True
    web_search._ddgs_last_call = 0.0
    # Real backoff and spacing would make the suite sleep for ~20s.
    monkeypatch.setattr(web_search, "_DDGS_BACKOFF", (0.0, 0.0, 0.0))
    monkeypatch.setattr(web_search, "_DDGS_MIN_INTERVAL", 0.0)
    yield
    web_search._ddgs_cache.clear()
    web_search._ddgs_import_state = None
    web_search._ddgs_last_call = 0.0


def _hits(n=2):
    return [{"title": f"t{i}", "href": f"https://e{i}.test", "body": f"b{i}"}
            for i in range(n)]


def _stub(monkeypatch, fn):
    monkeypatch.setattr(web_search, "_ddgs_text", fn)


# --------------------------------------------------------------------------
# Detection (issue 3a)
# --------------------------------------------------------------------------
def test_detection_rejects_an_unimportable_ddgs(monkeypatch):
    """find_spec was the wrong test: it stays True for a distribution whose Python
    files landed but whose native dependency did not, so /search doctor printed
    "ddgs importable: yes" while every search died on ImportError."""
    web_search._ddgs_import_state = None
    real_import = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "ddgs" or name.startswith("ddgs."):
            raise ImportError("libprimp.so: cannot open shared object file")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)
    assert web_search._ddgs_installed() is False


def test_detection_accepts_a_working_ddgs():
    web_search._ddgs_import_state = None
    assert web_search._ddgs_installed() is True


def test_detection_is_cached(monkeypatch):
    web_search._ddgs_import_state = None
    calls = []
    real_find = web_search.importlib.util.find_spec
    monkeypatch.setattr(web_search.importlib.util, "find_spec",
                        lambda n, *a, **k: (calls.append(n), real_find(n, *a, **k))[1])
    web_search._ddgs_installed()
    web_search._ddgs_installed()
    assert calls.count("ddgs") == 1


def test_unimportable_ddgs_fails_rather_than_raising(monkeypatch):
    web_search._ddgs_import_state = False
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert isinstance(out, SearchFailure)
    assert "importable" in out.error


# --------------------------------------------------------------------------
# Engines (issue 3b)
# --------------------------------------------------------------------------
def test_engines_are_named_explicitly(monkeypatch):
    """Upstream's default backend='auto' prioritises Wikipedia and Grokipedia,
    which answer almost none of the queries web_search exists for."""
    seen = {}

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        seen["backend"] = backend
        return _hits()

    _stub(monkeypatch, fake)
    DdgsProvider().search("q", 5, FakeCtx())
    assert seen["backend"] != "auto"
    assert seen["backend"].split(",")[0] == "duckduckgo", "general engines must come first"
    assert "wikipedia" in seen["backend"], "encyclopaedic fallback should still be present"
    assert seen["backend"].split(",")[-1] == "wikipedia", "...but last"


def test_only_engines_that_exist_in_ddgs_are_requested():
    """google/bing/yandex are NOT registered text engines in ddgs 9.x; requesting
    one would silently not do what it looks like."""
    from ddgs.engines import ENGINES
    available = set(ENGINES["text"])
    assert set(web_search._DDGS_ENGINE_ORDER) <= available, (
        f"unknown engines: {set(web_search._DDGS_ENGINE_ORDER) - available}")


def test_every_ordered_engine_has_a_host_entry():
    """A missing entry would be a silent egress-policy bypass."""
    for engine in web_search._DDGS_ENGINE_ORDER:
        assert web_search._DDGS_ENGINE_HOSTS.get(engine), engine


def test_hosts_match_the_installed_package():
    """Pins the map to reality so a ddgs bump that moves a URL fails here rather
    than at runtime under a fail-closed policy."""
    from ddgs.engines import ENGINES
    for engine, hosts in web_search._DDGS_ENGINE_HOSTS.items():
        cls = ENGINES["text"].get(engine)
        assert cls is not None, engine
        url = getattr(cls, "search_url", "") or ""
        if "{lang}" in url:      # wikipedia: pinned via region=us-en instead
            continue
        host = url.split("/")[2]
        assert any(host in h for h in hosts), f"{engine}: {host} not in {hosts}"


# --------------------------------------------------------------------------
# Hermes-documented call shape (issue 3f)
# --------------------------------------------------------------------------
def test_uses_context_manager_and_keyword_max_results():
    """Hermes documents both: `with DDGS(...) as ddgs` so the HTTP client and its
    connections are closed, and max_results ALWAYS as a keyword argument."""
    src = inspect.getsource(web_search._ddgs_text)
    assert "with DDGS(" in src, "DDGS must be used as a context manager"
    assert "max_results=" in src, "max_results must be passed as a keyword"


def test_region_is_pinned_so_the_wikipedia_host_stays_truthful():
    """wikipedia's search_url is https://{lang}.wikipedia.org/... A caller passing
    a non-en region would reach a host the allowlist never checked."""
    src = inspect.getsource(web_search._ddgs_text)
    assert "region=" in src


# --------------------------------------------------------------------------
# Throttling (issue 3c / 3d)
# --------------------------------------------------------------------------
def test_a_throttled_search_retries_and_can_still_succeed(monkeypatch):
    from ddgs.exceptions import RatelimitException
    calls = []

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        calls.append(1)
        if len(calls) < 2:
            raise RatelimitException("202 Ratelimit")
        return _hits()

    _stub(monkeypatch, fake)
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert isinstance(out, SearchSuccess) and out.results
    assert len(calls) == 2


def test_exhausted_retries_report_a_rate_limit(monkeypatch):
    from ddgs.exceptions import RatelimitException

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        raise RatelimitException("202 Ratelimit")

    _stub(monkeypatch, fake)
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert isinstance(out, SearchFailure)
    assert "rate limited" in out.error.lower()
    assert out.retryable is True


def test_digits_202_in_an_unrelated_message_is_not_a_rate_limit(monkeypatch):
    """The old check was `"202" in message`, which matches a year or a byte count,
    so unrelated failures were reported as throttling with throttling advice."""
    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        raise ValueError("failed to parse result from 2024 (2029 bytes)")

    _stub(monkeypatch, fake)
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert isinstance(out, SearchFailure)
    assert "rate limited" not in out.error.lower()


def test_a_non_throttle_failure_is_retried_too(monkeypatch):
    """A connection reset, an unwrapped timeout, or one engine's page layout
    changing is attempt-specific, not query-specific — retrying is not "the
    same thing again" since `backend` names several engines. Only running out
    of attempts (here, all 3) ends the search."""
    calls = []

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        calls.append(1)
        raise ValueError("malformed html")

    _stub(monkeypatch, fake)
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert len(calls) == 3
    assert isinstance(out, SearchFailure)
    assert "rate limited" not in out.error.lower()


def test_a_non_throttle_failure_can_still_succeed_on_a_later_attempt(monkeypatch):
    calls = []

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        calls.append(1)
        if len(calls) < 2:
            raise ConnectionResetError("connection reset by peer")
        return _hits()

    _stub(monkeypatch, fake)
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert isinstance(out, SearchSuccess) and out.results
    assert len(calls) == 2


def test_no_results_found_is_success_with_nothing(monkeypatch):
    """Upstream signals zero hits by raising. That is not a provider failure, and
    reporting it as one would make run_search shop for another backend."""
    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        raise RuntimeError("No results found for the query")

    _stub(monkeypatch, fake)
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert isinstance(out, SearchSuccess)
    assert out.results == []


def test_consecutive_distinct_searches_are_spaced(monkeypatch):
    """Hermes: 'DuckDuckGo may throttle after many rapid requests. Add a short
    delay between searches.' The cache covers repeats; this covers the loop that
    issues three DIFFERENT queries in a row."""
    slept = []
    monkeypatch.setattr(web_search, "_DDGS_MIN_INTERVAL", 2.0)
    monkeypatch.setattr(web_search.time, "sleep", lambda s: slept.append(s))
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None: _hits())
    provider = DdgsProvider()
    provider.search("first", 5, FakeCtx())
    provider.search("second", 5, FakeCtx())
    assert any(s > 0 for s in slept), "no delay inserted between distinct searches"


# --------------------------------------------------------------------------
# Repeat-query cache (issue 3c)
# --------------------------------------------------------------------------
def test_a_repeated_query_is_served_from_cache(monkeypatch):
    calls = []
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (calls.append(1), _hits())[1])
    provider = DdgsProvider()
    first = provider.search("same", 5, FakeCtx())
    second = provider.search("same", 5, FakeCtx())
    assert len(calls) == 1
    assert [r.url for r in first.results] == [r.url for r in second.results]


def test_a_cache_hit_costs_no_throttle_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(web_search, "_DDGS_MIN_INTERVAL", 5.0)
    monkeypatch.setattr(web_search.time, "sleep", lambda s: slept.append(s))
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None: _hits())
    provider = DdgsProvider()
    provider.search("same", 5, FakeCtx())
    slept.clear()
    provider.search("same", 5, FakeCtx())
    assert slept == [], "a cache hit must not pay the inter-search delay"


def test_a_different_limit_is_a_different_cache_key(monkeypatch):
    calls = []
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (calls.append(l), _hits())[1])
    provider = DdgsProvider()
    provider.search("same", 5, FakeCtx())
    provider.search("same", 10, FakeCtx())
    assert calls == [5, 10]


def test_an_expired_cache_entry_is_refetched(monkeypatch):
    calls = []
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (calls.append(1), _hits())[1])
    monkeypatch.setattr(web_search, "_DDGS_CACHE_TTL", 0)
    provider = DdgsProvider()
    provider.search("same", 5, FakeCtx())
    provider.search("same", 5, FakeCtx())
    assert len(calls) == 2


def test_the_cache_is_bounded(monkeypatch):
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None: _hits())
    monkeypatch.setattr(web_search, "_DDGS_CACHE_MAX", 4)
    provider = DdgsProvider()
    for i in range(12):
        provider.search(f"q{i}", 5, FakeCtx())
    assert len(web_search._ddgs_cache) <= 4


def test_an_empty_result_is_not_cached(monkeypatch):
    """Caching "nothing" for 5 minutes would hide a transient failure."""
    calls = []
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (calls.append(1), [])[1])
    provider = DdgsProvider()
    provider.search("q", 5, FakeCtx())
    provider.search("q", 5, FakeCtx())
    assert len(calls) == 2


# --------------------------------------------------------------------------
# Egress policy (issue 3e)
# --------------------------------------------------------------------------
def test_a_blocked_engine_is_dropped_not_fatal(monkeypatch):
    """Per-engine filtering, not all-or-nothing: the old check refused the whole
    backend if any one host was blocked, so widening the engine list under that
    rule would have made ddgs MORE likely to be denied."""
    seen = {}

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        seen["backend"] = backend
        return _hits()

    _stub(monkeypatch, fake)
    out = DdgsProvider().search("q", 5, FakeCtx(blocked_hosts=("duckduckgo.com",)))
    assert isinstance(out, SearchSuccess)
    assert "duckduckgo" not in seen["backend"]
    assert seen["backend"], "the remaining engines must still be used"


def test_every_engine_blocked_fails_closed_and_non_retryable(monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("the library must not be called when all engines are blocked")

    _stub(monkeypatch, must_not_run)
    all_hosts = tuple(h.split("//")[1] for h in web_search._DDGS_HOSTS)
    out = DdgsProvider().search("q", 5, FakeCtx(blocked_hosts=all_hosts))
    assert isinstance(out, SearchFailure)
    assert out.retryable is False, "a policy denial must not make run_search shop around"
    assert "egress policy" in out.error


def test_an_engine_is_dropped_if_any_of_its_hosts_is_blocked(monkeypatch):
    """yahoo reaches search.yahoo.com AND www.bing.com. Blocking either must drop
    the whole engine -- a partial check would let the library reach a denied host."""
    seen = {}
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (seen.update(backend=backend), _hits())[1])
    DdgsProvider().search("q", 5, FakeCtx(blocked_hosts=("www.bing.com",)))
    assert "yahoo" not in seen["backend"]


# --------------------------------------------------------------------------
# Proxy
# --------------------------------------------------------------------------
def test_search_proxy_is_passed_through(monkeypatch):
    """Hermes notes DuckDuckGo blocks some cloud IPs, which leaves a VPS install
    with no recourse."""
    seen = {}
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (seen.update(proxy=proxy), _hits())[1])
    ctx = FakeCtx(config={"search_proxy": "socks5h://127.0.0.1:9150"})
    DdgsProvider().search("q", 5, ctx)
    assert seen["proxy"] == "socks5h://127.0.0.1:9150"


def test_a_policy_blocked_proxy_is_dropped_not_honoured(monkeypatch):
    """A proxy is itself an egress destination. Failing open here would let a
    config key route around the operator's allowlist."""
    seen = {}
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (seen.update(proxy=proxy), _hits())[1])
    ctx = FakeCtx(blocked_hosts=("evil.example",),
                  config={"search_proxy": "http://evil.example:8080"})
    DdgsProvider().search("q", 5, ctx)
    assert seen["proxy"] is None


def test_no_proxy_configured_passes_none(monkeypatch):
    seen = {}
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None:
          (seen.update(proxy=proxy), _hits())[1])
    DdgsProvider().search("q", 5, FakeCtx())
    assert seen["proxy"] is None


# --------------------------------------------------------------------------
# Result shaping
# --------------------------------------------------------------------------
def test_results_are_capped_and_entries_without_a_url_are_dropped(monkeypatch):
    raw = _hits(5) + [{"title": "no url", "href": "", "body": "x"}]
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None: raw)
    out = DdgsProvider().search("q", 3, FakeCtx())
    assert len(out.results) == 3
    assert all(r.url for r in out.results)


def test_snippets_are_truncated(monkeypatch):
    raw = [{"title": "t", "href": "https://a.test", "body": "x" * 5000}]
    _stub(monkeypatch, lambda q, l, *, backend, timeout, proxy=None, region=None: raw)
    out = DdgsProvider().search("q", 5, FakeCtx())
    assert len(out.results[0].snippet) <= web_search.MAX_SNIPPET_CHARS
