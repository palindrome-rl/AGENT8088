"""Web search fallbacks are visible and recover: quality checks, the auto
fall-through, re-probing SearXNG, the model-facing caveat, and every surface
that reads capabilities.SEARCH (banner, /status, /doctor, /api/*).

No network: providers are fakes, ddgs is stubbed at _ddgs_text, HTTP at
_http_json, and the SearXNG probe is always injected or patched.
"""
import io
import json
import time
import urllib.error

import pytest

from agent8088 import capabilities as C
from agent8088 import web_search
from agent8088.web_search import (DdgsProvider, Registry, SearchFailure, SearchReport,
                                  SearchResult, SearchSuccess, TavilyProvider)


class Ctx:
    def __init__(self, secrets=None, config=None):
        self.secrets = dict(secrets or {})
        self.config = dict(config or {})

    def check_url(self, url):
        return ""

    def get_secret(self, name):
        return self.secrets.get(name, "")

    def wrap(self, text, source=""):
        return text


def _results(n, domain=None):
    return [SearchResult(f"t{i}", f"https://{domain or f'e{i}.test'}/{i}", "s")
            for i in range(n)]


class Fake:
    """A provider with a fixed answer. check_quality mimics ddgs."""

    def __init__(self, name, outcome, check_quality=False, images=False, alternate=None):
        self.name = name
        self.outcome = outcome
        self.calls = 0
        self.check_quality = check_quality
        self.alternate = alternate
        self.alternate_calls = 0
        if images:
            self.search = self._search_images

    def is_available(self, ctx):
        return True

    def setup_hint(self):
        return self.name

    def search(self, query, limit, ctx):
        self.calls += 1
        return self.outcome

    def _search_images(self, query, limit, ctx, images=False):
        self.calls += 1
        return self.outcome

    def search_alternate(self, query, limit, ctx, first):
        self.alternate_calls += 1
        return self.alternate


@pytest.fixture(autouse=True)
def _quiet_ddgs(monkeypatch):
    web_search._ddgs_cache.clear()
    web_search._ddgs_import_state = True
    web_search._ddgs_last_call = 0.0
    monkeypatch.setattr(web_search, "_DDGS_BACKOFF", (0.0, 0.0, 0.0))
    monkeypatch.setattr(web_search, "_DDGS_MIN_INTERVAL", 0.0)
    yield
    web_search._ddgs_cache.clear()
    web_search._ddgs_import_state = None


# --- assess_results -----------------------------------------------------------------

def test_assess_results():
    assert web_search.assess_results([], 5) == "no results"
    assert web_search.assess_results(_results(2), 5) == "only 2 results"
    assert web_search.assess_results(_results(2), 2) == "", "a limit of 2 is satisfied by 2"
    assert web_search.assess_results(_results(5), 5) == ""
    assert web_search.assess_results(_results(5, "www.one.test"), 5) == \
        "5 of 5 results from one.test"
    mixed = _results(4, "one.test") + _results(1)
    assert web_search.assess_results(mixed, 5) == "4 of 5 results from one.test"


# --- images: signature, not TypeError -----------------------------------------------

def test_images_are_passed_only_to_providers_that_take_them():
    capable = Fake("searxng", SearchSuccess(_results(3), "searxng"), images=True)
    out = web_search.run_search("q", 5, Registry([capable]), {}, Ctx(), images=True)
    assert "images not supported" not in out


def test_images_unsupported_is_said_in_the_result():
    plain = Fake("tavily", SearchSuccess(_results(3), "tavily"))
    out = web_search.run_search("q", 5, Registry([plain]), {}, Ctx(), images=True)
    assert plain.calls == 1
    assert "[note: images not supported by tavily; these are web results]" in out


def test_a_providers_own_type_error_is_not_run_twice():
    calls = []

    class Buggy:
        name = "buggy"

        def search(self, query, limit, ctx, images=False):
            calls.append(1)
            raise TypeError("bug inside the provider")

    with pytest.raises(TypeError):
        web_search.run_search("q", 5, Registry([Buggy()]), {}, Ctx(), images=True,
                              chain=[Buggy()])
    assert calls == [1]


# --- weak ddgs answers -----------------------------------------------------------------

def test_a_weak_ddgs_answer_is_retried_with_another_engine_group():
    better = SearchSuccess(_results(5), "ddgs", notes=["ddgs retried with engines starting at mojeek"])
    ddgs = Fake("ddgs", SearchSuccess(_results(1), "ddgs"), check_quality=True, alternate=better)
    report = web_search.run_search("q", 5, Registry([ddgs]), {}, Ctx(), return_report=True)
    assert ddgs.alternate_calls == 1
    assert report.provider == "ddgs" and report.weak == ""
    assert "[note: ddgs retried with engines starting at mojeek]" in report.text


def test_a_still_weak_ddgs_answer_hands_off_to_the_next_backend():
    ddgs = Fake("ddgs", SearchSuccess(_results(1), "ddgs"), check_quality=True)
    tavily = Fake("tavily", SearchSuccess(_results(5), "tavily"))
    report = web_search.run_search("q", 5, Registry([]), {}, Ctx(), chain=[ddgs, tavily],
                                   return_report=True)
    assert report.provider == "tavily"
    assert "Search results (via tavily)" in report.text
    assert "fell back after ddgs: only 1 result — served by tavily" in report.text
    assert report.tried == ("ddgs", "tavily")


def test_the_best_weak_answer_is_kept_when_nothing_better_turns_up():
    ddgs = Fake("ddgs", SearchSuccess(_results(2), "ddgs"), check_quality=True)
    searxng = Fake("searxng", SearchFailure("Could not reach SearXNG"))
    report = web_search.run_search("q", 5, Registry([]), {}, Ctx(), chain=[ddgs, searxng],
                                   return_report=True)
    assert report.provider == "ddgs"
    assert report.weak == "only 2 results"
    assert "Search results (via ddgs)" in report.text
    assert "[note: results may be incomplete (only 2 results); tried: ddgs, searxng]" in report.text


def test_a_short_answer_from_a_non_scraper_is_not_second_guessed():
    searxng = Fake("searxng", SearchSuccess(_results(1), "searxng"))
    ddgs = Fake("ddgs", SearchSuccess(_results(5), "ddgs"), check_quality=True)
    report = web_search.run_search("q", 5, Registry([]), {}, Ctx(), chain=[searxng, ddgs],
                                   return_report=True)
    assert report.provider == "searxng" and ddgs.calls == 0


def test_no_results_everywhere_says_so_and_names_what_was_tried():
    a = Fake("searxng", SearchSuccess([], "searxng"))
    b = Fake("ddgs", SearchSuccess([], "ddgs"), check_quality=True)
    report = web_search.run_search("q", 5, Registry([]), {}, Ctx(), chain=[a, b],
                                   return_report=True)
    assert report.text.startswith("No results found (tried: searxng, ddgs)")
    assert report.failed == ("searxng", "ddgs")


def test_ddgs_search_alternate_rotates_engines_and_merges(monkeypatch):
    seen = []

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        seen.append(backend)
        if len(seen) == 1:
            return [{"title": "a", "href": "https://a.test", "body": ""}]
        return [{"title": "a", "href": "https://a.test", "body": ""},
                {"title": "b", "href": "https://b.test", "body": ""},
                {"title": "c", "href": "https://c.test", "body": ""}]

    monkeypatch.setattr(web_search, "_ddgs_text", fake)
    report = web_search.run_search("q", 5, Registry([DdgsProvider()]), {"web_search_provider": "ddgs"},
                                   Ctx(), return_report=True)
    assert len(seen) == 2
    assert seen[0].startswith("duckduckgo") and not seen[1].startswith("duckduckgo")
    assert report.provider == "ddgs" and report.weak == ""
    assert report.text.count("https://a.test") == 1
    assert "https://c.test" in report.text


def test_ddgs_nothing_after_throttling_is_not_reported_as_no_results(monkeypatch):
    from ddgs.exceptions import RatelimitException
    attempts = []

    def fake(query, limit, *, backend, timeout, proxy=None, region=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise RatelimitException("202 Ratelimit")
        raise RuntimeError("No results found.")

    monkeypatch.setattr(web_search, "_ddgs_text", fake)
    out = DdgsProvider().search("q", 5, Ctx())
    assert isinstance(out, SearchFailure) and out.retryable
    assert "may be throttled" in out.error
    assert "no results found" not in out.error.lower()


# --- Tavily/Exa: one retry on 429/5xx --------------------------------------------------

def _http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://api.tavily.com/search", code, "x", headers, io.BytesIO())


@pytest.fixture
def slept(monkeypatch):
    calls = []
    monkeypatch.setattr(web_search.time, "sleep", lambda s: calls.append(s))
    return calls


def _scripted_http(monkeypatch, script):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["url"])
        item = script[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(web_search, "_http_json", fake)
    return calls


def test_keyed_429_is_retried_once_honouring_retry_after(monkeypatch, slept):
    calls = _scripted_http(monkeypatch, [
        _http_error(429, "2"),
        {"results": [{"title": "t", "url": "https://a.test", "content": "c"}]}])
    out = TavilyProvider().search("q", 5, Ctx({"TAVILY_API_KEY": "k"}))
    assert isinstance(out, SearchSuccess) and len(calls) == 2
    assert slept == [2.0]


def test_keyed_retry_after_is_capped(monkeypatch, slept):
    _scripted_http(monkeypatch, [_http_error(503, "120"), _http_error(503, "120")])
    out = TavilyProvider().search("q", 5, Ctx({"TAVILY_API_KEY": "k"}))
    assert slept == [web_search.KEYED_RETRY_CAP]
    assert isinstance(out, SearchFailure) and out.retryable, "falls to the next backend"


def test_keyed_credential_rejection_is_not_retried(monkeypatch, slept):
    calls = _scripted_http(monkeypatch, [_http_error(401)])
    out = TavilyProvider().search("q", 5, Ctx({"TAVILY_API_KEY": "k"}))
    assert len(calls) == 1 and slept == [] and out.retryable is False


# --- run_search report shape --------------------------------------------------------

def test_return_report_and_legacy_failures_tuple_agree():
    down = Fake("searxng", SearchFailure("Could not reach SearXNG"))
    report = web_search.run_search("q", 5, Registry([down]), {"web_search_provider": "searxng"},
                                   Ctx(), return_report=True)
    assert isinstance(report, SearchReport)
    assert report.provider == "" and report.failed == ("searxng",)
    assert report.failures == ("searxng: Could not reach SearXNG",)
    text, failed = web_search.run_search("q", 5, Registry([down]),
                                         {"web_search_provider": "searxng"}, Ctx(),
                                         return_failures=True)
    assert failed == ("searxng",) and text == report.text


# ======================================================================================
# Engine: reporting, auto fall-through, re-probe, model note
# ======================================================================================
LOCAL = "http://127.0.0.1:8888/search?q="


@pytest.fixture
def eng(engine, monkeypatch):
    monkeypatch.setattr(engine, "load_env_file", lambda path=None: {})
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    # Never touch the network from a re-probe.
    monkeypatch.setattr(engine.web_search, "probe_searxng", lambda ctx, timeout=None: False)
    return engine


def _registry(eng, monkeypatch, *providers):
    monkeypatch.setattr(eng, "WEB_SEARCH_REGISTRY", Registry(list(providers)))


def _local_searxng(eng, no_prompt="1"):
    eng.APP_CONFIG["search_base_url"] = LOCAL
    eng.SEARCH_BASE_URL_CONFIGURED = True
    eng.APP_CONFIG["web_search_no_prompt"] = no_prompt


def _search(eng, query="test query"):
    return eng.exec_tool("web_search", json.dumps({"query": query}))


def test_auto_resolving_to_ddgs_reports_search_degraded(eng):
    _local_searxng(eng)
    eng.APP_CONFIG["web_search_provider"] = "auto"
    assert eng.resolve_auto_search_provider(probe=lambda ctx: False) == "ddgs"
    entry = C.get(C.SEARCH)
    assert (entry.state, entry.active, entry.reason) == (C.DEGRADED, "ddgs", "SearXNG not answering")
    assert "/search setup" in entry.fix and "TAVILY_API_KEY" in entry.fix
    assert "search=ddgs" in C.banner_line()


def test_auto_resolving_to_searxng_is_ok(eng):
    _local_searxng(eng)
    eng.APP_CONFIG["web_search_provider"] = "auto"
    assert eng.resolve_auto_search_provider(probe=lambda ctx: True) == "searxng"
    assert C.get(C.SEARCH).state == C.OK
    assert C.banner_line() == ""


def test_auto_with_no_searxng_and_no_docker_says_why(eng, monkeypatch):
    monkeypatch.setattr(eng.shutil, "which", lambda name: None)
    eng.APP_CONFIG["web_search_provider"] = "auto"
    eng.resolve_auto_search_provider(probe=lambda ctx: False)
    entry = C.get(C.SEARCH)
    assert entry.reason == "no SearXNG; Docker not found"
    assert entry.fix.startswith("/search setup (needs Docker)")


def test_auto_with_nothing_available_is_unavailable(eng, monkeypatch):
    monkeypatch.setattr(eng.web_search, "_ddgs_installed", lambda: False)
    eng.APP_CONFIG["web_search_provider"] = "auto"
    assert eng.resolve_auto_search_provider(probe=lambda ctx: False) == ""
    assert C.get(C.SEARCH).state == C.UNAVAILABLE


def test_a_keyed_backend_is_ok(eng, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    eng.APP_CONFIG["web_search_provider"] = "auto"
    assert eng.resolve_auto_search_provider(probe=lambda ctx: False) == "tavily"
    assert C.get(C.SEARCH).state == C.OK


def test_a_second_resolve_keeps_autos_report(eng):
    """The web server's lifespan resolves again after cli.main already did."""
    _local_searxng(eng)
    eng.APP_CONFIG["web_search_provider"] = "auto"
    eng.resolve_auto_search_provider(probe=lambda ctx: False)
    eng.resolve_auto_search_provider(probe=lambda ctx: False)
    assert C.get(C.SEARCH).reason == "SearXNG not answering"


def _auto_pinned_searxng(eng, monkeypatch, searxng_outcome, ddgs_outcome):
    searxng = Fake("searxng", searxng_outcome, images=True)
    ddgs = Fake("ddgs", ddgs_outcome)
    _registry(eng, monkeypatch, searxng, ddgs)
    _local_searxng(eng)
    eng.APP_CONFIG["web_search_provider"] = "auto"
    eng.resolve_auto_search_provider(probe=lambda ctx: True)
    assert eng.APP_CONFIG["web_search_provider"] == "searxng"
    return searxng, ddgs


def test_auto_falls_through_when_searxng_dies_mid_session(eng, monkeypatch):
    """Default config (auto + web_search_no_prompt=1 + local SearXNG): a SearXNG
    that stops answering hands this search to ddgs with no prompt — the same
    outcome as SearXNG being down at startup — and the pin moves to ddgs."""
    seen = []
    C.subscribe(seen.append)
    searxng, ddgs = _auto_pinned_searxng(
        eng, monkeypatch, SearchFailure("Could not reach SearXNG at 127.0.0.1:8888"),
        SearchSuccess(_results(5), "ddgs"))
    result = _search(eng)
    assert not result.startswith("ESCALATION_REQUEST")
    assert "Search results (via ddgs)" in result
    assert "fell back after searxng: Could not reach SearXNG" in result
    assert result.rstrip().endswith(eng.SEARCH_DDGS_NOTE)
    assert eng.APP_CONFIG["web_search_provider"] == "ddgs"
    entry = C.get(C.SEARCH)
    assert (entry.state, entry.reason) == (C.DEGRADED, "SearXNG stopped answering")
    assert [c.message for c in seen] == [
        "search switched to ddgs (SearXNG stopped answering) — keyless scraper: results "
        "can be incomplete, throttled or less relevant; " + entry.fix]
    # The next search goes straight to ddgs.
    _search(eng, "second query")
    assert searxng.calls == 1 and ddgs.calls == 2


def test_an_explicit_searxng_pin_still_asks_before_leaving_the_network(eng, monkeypatch):
    searxng = Fake("searxng", SearchFailure("Could not reach SearXNG"), images=True)
    ddgs = Fake("ddgs", SearchSuccess(_results(5), "ddgs"))
    _registry(eng, monkeypatch, searxng, ddgs)
    _local_searxng(eng)
    eng.set_search_provider("searxng")
    result = _search(eng)
    assert result.startswith("ESCALATION_REQUEST\x1f")
    assert result.split("\x1f")[2] == "network_fallback"
    assert ddgs.calls == 0
    assert C.get(C.SEARCH).state == C.UNAVAILABLE
    assert eng.APP_CONFIG["web_search_provider"] == "searxng"


def test_a_prompt_free_call_never_falls_through_to_a_keyed_vendor(eng, monkeypatch):
    _local_searxng(eng)
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    eng._SEARCH_AUTO["pin"] = "searxng"
    eng.APP_CONFIG["web_search_provider"] = "searxng"
    ctx = eng._search_context()
    free = [p.name for p in eng._search_call_chain(ctx, prompt_free=True)]
    approved = [p.name for p in eng._search_call_chain(ctx, prompt_free=False)]
    assert free == ["searxng", "ddgs"]
    assert approved == ["searxng", "tavily", "ddgs"]


def test_a_ddgs_answer_carries_the_model_note(eng, monkeypatch):
    _registry(eng, monkeypatch, Fake("ddgs", SearchSuccess(_results(5), "ddgs")))
    eng.set_search_provider("ddgs")
    result = _search(eng)
    assert eng.SEARCH_DDGS_NOTE in result
    assert C.get(C.SEARCH).reason == "pinned: web_search_provider=ddgs"


def test_a_searxng_answer_carries_no_note(eng, monkeypatch):
    _auto_pinned_searxng(eng, monkeypatch, SearchSuccess(_results(5), "searxng"),
                         SearchSuccess(_results(5), "ddgs"))
    result = _search(eng)
    assert "[note:" not in result
    assert C.get(C.SEARCH).state == C.OK


def _auto_on_ddgs(eng, monkeypatch, last_probe):
    searxng = Fake("searxng", SearchSuccess(_results(5), "searxng"), images=True)
    ddgs = Fake("ddgs", SearchSuccess(_results(5), "ddgs"))
    _registry(eng, monkeypatch, searxng, ddgs)
    _local_searxng(eng)
    eng.APP_CONFIG["web_search_provider"] = "auto"
    eng.resolve_auto_search_provider(probe=lambda ctx: False)
    # 0.0 means "long ago". The interval is measured on time.monotonic(), which
    # starts near zero on a freshly booted machine (a CI runner), so a literal 0
    # there is less than 300 s ago and no reprobe is due.
    eng._SEARCH_AUTO["last_probe"] = time.monotonic() - 100_000 if last_probe == 0.0 else last_probe
    return searxng, ddgs


def test_reprobe_switches_back_up_when_searxng_answers_again(eng, monkeypatch):
    seen = []
    searxng, ddgs = _auto_on_ddgs(eng, monkeypatch, last_probe=time.monotonic() - 301)
    C.subscribe(seen.append)
    monkeypatch.setattr(eng.web_search, "probe_searxng", lambda ctx, timeout=None: True)
    result = _search(eng)
    assert "Search results (via searxng)" in result
    assert eng.APP_CONFIG["web_search_provider"] == "searxng"
    assert C.get(C.SEARCH).state == C.OK
    assert [c.message for c in seen] == ["search upgraded back to searxng"]
    assert ddgs.calls == 0


def test_reprobe_is_rate_limited(eng, monkeypatch):
    probes = []
    _auto_on_ddgs(eng, monkeypatch, last_probe=time.monotonic())
    monkeypatch.setattr(eng.web_search, "probe_searxng",
                        lambda ctx, timeout=None: probes.append(1) or True)
    _search(eng)
    assert probes == []
    assert eng.APP_CONFIG["web_search_provider"] == "ddgs"


def test_reprobe_can_be_disabled(eng, monkeypatch):
    _auto_on_ddgs(eng, monkeypatch, last_probe=0.0)
    eng.APP_CONFIG["web_search_reprobe_seconds"] = "0"
    assert eng._maybe_reprobe_searxng(probe=lambda ctx: True) is False


def test_reprobe_failing_keeps_ddgs_and_waits_again(eng, monkeypatch):
    _auto_on_ddgs(eng, monkeypatch, last_probe=0.0)
    assert eng._maybe_reprobe_searxng(probe=lambda ctx: False) is False
    assert eng._SEARCH_AUTO["last_probe"] > 0
    assert eng._maybe_reprobe_searxng(probe=lambda ctx: True) is False, "interval restarted"


def test_an_explicit_pin_is_never_reprobed(eng, monkeypatch):
    _local_searxng(eng)
    eng.set_search_provider("ddgs")
    eng._SEARCH_AUTO["last_probe"] = 0.0
    assert eng._maybe_reprobe_searxng(probe=lambda ctx: True) is False


def test_set_search_provider_auto_resolves(eng, monkeypatch):
    _local_searxng(eng)
    picked = eng.set_search_provider("auto")
    assert picked == "ddgs" and eng.APP_CONFIG["web_search_provider"] == "ddgs"
    assert eng._search_auto_active()


def test_describe_capabilities_lists_what_is_limited(eng):
    _local_searxng(eng)
    eng.set_search_provider("auto")
    text = eng.describe_capabilities()
    assert "## Limited right now" in text
    assert "- search: degraded, using ddgs — SearXNG not answering" in text
    assert "- Web search: ddgs (degraded: SearXNG not answering)" in text


# ======================================================================================
# CLI and web surfaces
# ======================================================================================

@pytest.fixture
def cli(eng):
    from agent8088 import cli as cli_mod
    return cli_mod


class _Rec:
    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(getattr(a, "plain", a)) for a in args))

    def text(self):
        return "\n".join(self.lines)


def _degrade():
    C.report(C.SEARCH, active="ddgs", preferred="searxng", state=C.DEGRADED,
             reason="SearXNG not answering", impact="keyless scraper", fix="/search setup")


def test_startup_notices_include_the_limited_line(cli, monkeypatch):
    monkeypatch.setattr(cli.A, "CONFIG_WARNINGS", [])
    monkeypatch.setattr(cli, "_mcp_failure_notice", lambda: [])
    _degrade()
    # printed under a "! " marker, so the line drops its own ⚠
    assert "limited: search=ddgs — /doctor" in cli._startup_notices()


def test_cli_prints_queued_changes_once_at_a_safe_point(cli, monkeypatch):
    rec = _Rec()
    monkeypatch.setattr(cli, "console", rec)
    unsubscribe = C.subscribe(cli._queue_capability_change)
    try:
        _degrade()
        assert rec.lines == [], "the subscriber queues; it never prints mid-render"
        cli._print_capability_changes()
        cli._print_capability_changes()
    finally:
        unsubscribe()
    assert rec.lines == ["  ⚠ search using ddgs (SearXNG not answering) — "
                         "keyless scraper; /search setup"]


def test_cli_drops_a_round_trip_between_safe_points(cli, monkeypatch):
    rec = _Rec()
    monkeypatch.setattr(cli, "console", rec)
    unsubscribe = C.subscribe(cli._queue_capability_change)
    try:
        _degrade()
        C.report(C.SEARCH, active="searxng")
        cli._print_capability_changes()
    finally:
        unsubscribe()
    assert rec.lines == []


def test_cli_prints_the_recovery(cli, monkeypatch):
    rec = _Rec()
    monkeypatch.setattr(cli, "console", rec)
    _degrade()
    unsubscribe = C.subscribe(cli._queue_capability_change)
    try:
        C.report(C.SEARCH, active="searxng")
        cli._print_capability_changes()
    finally:
        unsubscribe()
    assert rec.lines == ["  ✓ search upgraded back to searxng"]


def test_status_has_search_and_limited_rows(cli, monkeypatch):
    from rich.console import Console
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=200, color_system=None))
    _degrade()
    C.report(C.MEMORY, active="keyword-only", state=C.DEGRADED, reason="no embedder")
    cli.cmd_status("")
    text = out.getvalue()
    assert "Web search" in text and "ddgs (degraded)" in text
    assert "Limited" in text and "memory: keyword-only — no embedder" in text


def test_doctor_search_row_reports_the_active_backend(cli):
    assert cli._doctor_search_check()["status"] == "ok"
    _degrade()
    row = cli._doctor_search_check()
    assert row["status"] == "warn"
    assert row["detail"] == "degraded: ddgs — SearXNG not answering; keyless scraper"
    assert row["fix"] == "/search setup"
    C.report(C.SEARCH, active="searxng")
    assert cli._doctor_search_check()["detail"] == "ok · searxng"


def test_doctor_lists_other_degraded_capabilities(cli):
    _degrade()
    C.report(C.MEMORY, active="keyword-only", state=C.DEGRADED, reason="no embedder",
             fix="ollama pull nomic-embed-text")
    C.report(C.MODEL, active="", state=C.UNAVAILABLE, reason="endpoint down")
    rows = {r["name"]: r for r in cli._doctor_capability_checks()}
    assert set(rows) == {"Limited: memory", "Limited: model"}, "search has its own row"
    assert rows["Limited: memory"]["status"] == "warn"
    assert rows["Limited: memory"]["fix"] == "ollama pull nomic-embed-text"
    assert rows["Limited: model"]["status"] == "fail", "the model is core"


def test_doctor_report_exposes_limited_rows_and_a_degraded_flat_field(cli, monkeypatch):
    monkeypatch.setattr(cli, "doctor_checks", lambda live=True: [cli._doctor_search_check()])
    _degrade()
    report = cli.doctor_report()
    assert report["web_search"] == "limited: ddgs (fallback)"
    assert report["limited"][0]["name"] == "search"
    assert report["ok"] is True, "a degraded optional feature is not a failure"


def test_api_status_and_api_doctor_carry_the_rows(cli, monkeypatch):
    from fastapi.testclient import TestClient
    from agent8088 import web_server
    monkeypatch.setattr(web_server, "_eng", lambda: cli.A)
    monkeypatch.setattr(web_server, "_cl", lambda: cli)
    monkeypatch.setattr(cli, "doctor_checks", lambda live=True: [cli._doctor_search_check()])
    _degrade()
    client = TestClient(web_server.app)
    status = client.get("/api/status").json()
    # Other capabilities (a context limit, say) may be registered too, in any order.
    search = [row for row in status["capabilities"] if row["name"] == "search"]
    assert len(search) == 1 and search[0]["state"] == "degraded"
    limited = [row for row in client.get("/api/doctor").json()["limited"] if row["name"] == "search"]
    assert len(limited) == 1 and limited[0]["active"] == "ddgs"


def test_api_search_use_resolves_auto(cli, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from agent8088 import web_server
    monkeypatch.setattr(web_server, "_eng", lambda: cli.A)
    monkeypatch.setattr(cli.A, "CONFIG_PATH", tmp_path / "config.txt")
    monkeypatch.setattr(cli.A, "_docker_available", lambda: False)
    from agent8088 import searxng_provision
    monkeypatch.setattr(searxng_provision, "status", lambda *a, **k: {"detail": "n/a"})
    body = TestClient(web_server.app).post("/api/search/use", json={"provider": "auto"}).json()
    assert cli.A.APP_CONFIG["web_search_provider"] == "ddgs"
    assert body["selected"] == "ddgs"
    assert C.get(C.SEARCH).state == C.DEGRADED
