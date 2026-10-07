"""Web search provider registry.

One ``web_search`` tool, several interchangeable backends. Which backend serves
a call is decided here, not by the model picking between similarly-named tools
and landing on the one that has no credential configured.

Roles:
  searxng   default   self-hosted, no key (see searxng_provision.py)
  tavily    optional  enabled by TAVILY_API_KEY
  exa       optional  enabled by EXA_API_KEY
  ddgs      fallback  keyless, ships as a dependency — rotates several engines

Selection precedence (mirrors Hermes' agent/web_search_registry.py):

  1. ``web_search_provider=<name>`` in config.txt — explicit, no fallback.
  2. A keyed backend (tavily, exa) whose API key is configured jumps to the
     front of PREFERENCE — adding a key is a signal to prefer it. tavily wins
     the tie if both are configured.
  3. PREFERENCE order below for everything else, filtered by availability.
  4. Nothing available — the tool returns an actionable setup error.

Unlike Hermes, which only *selects* a backend, run_search() also *falls
through* the chain at call time: a backend that is configured but broken
(instance stopped, rate-limited) must not mean "no web search". Because ddgs is
bundled, the chain is effectively never empty. Under ``auto`` the engine hands
run_search an explicit chain (the pin, then the rest of the auto order) so a
pinned SearXNG that stops answering falls through too — see
engine._search_call_chain.

ddgs results are quality-checked (assess_results): a thin or one-domain answer
is retried with a different engine group and then handed down the chain before
it is accepted, and the result says which backends were tried.

This module deliberately does NOT import engine.py — that would be circular.
Security guards (egress policy, SSRF, outbound-secret check, untrusted-content
wrapping) are injected via SearchContext so engine.py remains the single
enforcement point and no provider can quietly skip it.
"""
from __future__ import annotations

import abc
import importlib.util
import inspect
import ipaddress
import json as _json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

# Base order when no keyed backend is configured: searxng first (self-hosted, no
# key), then ddgs. ddgs scrapes result pages rather than using an API, so it is
# still the backend most likely to throttle under sustained use — but it no longer
# gives up when it does: it rotates several engines (separate throttle buckets),
# retries a throttled attempt with backoff, spaces consecutive calls, and serves
# repeat queries from a short cache. tavily/exa sit at the end of this base order,
# but Registry.chain() promotes either one to the front — ahead of searxng and
# ddgs — the moment its API key is configured; adding a key is a signal to prefer
# that backend. tavily wins the tie if both are configured.
PREFERENCE = ("searxng", "ddgs", "tavily", "exa")

# web_search_provider=auto — "pick the best available at startup and pin it".
# A pin is what keeps the approval-free local-SearXNG path well-defined, so AUTO
# deliberately RESOLVES to a real name at startup instead of staying dynamic.
# The engine still lets auto's pin fall through mid-session (an explicit chain
# passed to run_search) and re-probes SearXNG to switch back up.
AUTO = "auto"

# This runs on the startup path, so an unreachable instance must not stall
# launch. Startup only — never per search.
#
# Two budgets, because "unreachable" fails in two very different ways. A remote
# instance behind a dropping firewall hangs for the whole timeout, so its budget
# has to stay short. A loopback instance cannot: with nothing listening the
# connection is refused immediately (measured at 0.2ms), so the budget only ever
# applies to an instance that is actually answering — and a SearXNG search fans
# out to every configured engine, which measured 0.96s-3.0s locally. Three
# seconds sat on that boundary and made startup selection a coin flip: an
# overrunning probe pinned ddgs for the session while /search status, probing
# again later, reported searxng available.
STARTUP_PROBE_TIMEOUT = 3
LOCAL_PROBE_TIMEOUT = 10

MAX_SEARCH_BYTES = 2 * 1024 * 1024
MAX_SNIPPET_CHARS = 400
HTTP_TIMEOUT = 20


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class SearchSuccess:
    results: list
    provider: str
    # Short, trusted remarks rendered after the results ("retried with other
    # engines", "images not supported by ddgs"). Never untrusted page text.
    notes: list = field(default_factory=list)


@dataclass
class SearchFailure:
    error: str
    # Retryable failures (outage, rate limit) let run_search try the next
    # backend. Non-retryable ones (a guard denial, a bad credential) stop the
    # chain: trying another vendor would be routing around a decision, not
    # around an outage.
    retryable: bool = True


@dataclass
class SearchReport:
    """Everything run_search knows about one call (``return_report=True``).

    The engine needs more than the text: which backend served (to report the
    capability state and pick the model note), and which ones failed (to
    decide on a fallback prompt or a pin change).
    """
    text: str
    provider: str = ""          # who served; "" when nothing did
    tried: tuple = ()
    failed: tuple = ()          # retryable failures and empty answers, in order
    failures: tuple = ()        # "name: error" strings, same order as failed
    weak: str = ""              # why the served answer is thin, if it is


@dataclass
class SearchContext:
    """Config, credentials, and security guards handed to every provider.

    check_url MUST be called by every provider before every outbound request;
    it returns None when the URL is permitted, else an error string to surface
    verbatim. wrap wraps result text as untrusted external content.
    """
    config: dict = field(default_factory=dict)
    get_secret: Callable[[str], str] = lambda name: ""
    check_url: Callable[[str], str | None] = lambda url: None
    wrap: Callable[..., str] = lambda text, source="": text


# ---------------------------------------------------------------------------
# HTTP helpers. Callers MUST have run ctx.check_url(url) first — these perform
# no policy checks of their own.
# ---------------------------------------------------------------------------
def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_SEARCH_BYTES + 1)
    if len(raw) > MAX_SEARCH_BYTES:
        raise ValueError("search response exceeded size limit")
    return _json.loads(raw.decode("utf-8", errors="replace"))


def _http_json(*, url, method="GET", headers=None, body=None, timeout=HTTP_TIMEOUT):
    data = _json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_SEARCH_BYTES + 1)
    if len(raw) > MAX_SEARCH_BYTES:
        raise ValueError("search response exceeded size limit")
    return _json.loads(raw.decode("utf-8", errors="replace"))


def _is_local_host(host: str) -> bool:
    host = (host or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------
class WebSearchProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    def is_available(self, ctx: SearchContext) -> bool:
        """Cheap, synchronous, no-network check: is this provider configured?

        Liveness is deliberately NOT checked here — a health ping on every
        call would double latency and still race. A configured-but-dead
        backend is discovered by searching and falling through.
        """

    @abc.abstractmethod
    def setup_schema(self) -> dict:
        """Data the setup UI needs to enable this provider.

        Shape borrowed from Hermes' provider ``get_setup_schema()`` so /search
        and the wizard render from provider-owned data instead of a hardcoded
        list that drifts out of sync.
        """

    @abc.abstractmethod
    def search(self, query: str, limit: int, ctx: SearchContext):
        """Return SearchSuccess or SearchFailure. Must never raise."""

    def setup_hint(self) -> str:
        """One-line rendering of setup_schema, for error messages."""
        schema = self.setup_schema()
        keys = ", ".join(v["key"] for v in schema.get("env_vars") or [])
        detail = f"set {keys}" if keys else schema.get("tag", "")
        return f"{self.name} ({schema.get('badge', '')}) — {detail}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class Registry:
    def __init__(self, providers):
        self._providers = {p.name: p for p in providers}

    def get(self, name: str):
        return self._providers.get(name)

    def names(self):
        return list(self._providers)

    def all(self):
        """Every registered provider in PREFERENCE order, available or not.

        Used by /search status so optional backends stay discoverable rather
        than invisible until a key happens to be set.
        """
        ordered = [self._providers[n] for n in PREFERENCE if n in self._providers]
        extra = [p for n, p in self._providers.items() if n not in PREFERENCE]
        return ordered + extra

    def _dynamic_order(self, ctx) -> list:
        """PREFERENCE, with any available keyed backend (tavily, exa) moved to
        the front — ahead of searxng and ddgs. Only a *configured* keyed
        backend is promoted; is_available() is the same check chain() already
        filters on, so an unconfigured one is simply skipped, not demoted."""
        promoted = [n for n in ("tavily", "exa")
                    if n in self._providers and self._providers[n].is_available(ctx)]
        rest = [n for n in PREFERENCE if n not in promoted]
        return promoted + rest

    def chain(self, config: dict, ctx) -> list:
        """Ordered providers to attempt for one search call."""
        explicit = str(config.get("web_search_provider") or "").strip().lower()
        # AUTO is resolved to a concrete name at startup (see engine's
        # resolve_auto_search_provider). Reaching here still set to "auto" means
        # resolution never ran — an embedder that skipped startup. Fall back to
        # the full chain rather than to "unknown provider": search keeps working,
        # and because the pin is unresolved the no-prompt exemption stays OFF,
        # which is the safe direction to fail.
        if explicit and explicit != AUTO:
            provider = self._providers.get(explicit)
            return [provider] if provider else []
        return [self._providers[n] for n in self._dynamic_order(ctx)
                if n in self._providers and self._providers[n].is_available(ctx)]

    def auto_order(self, ctx) -> list:
        """Names the auto chain would try, best first, filtered by is_available()
        (no network). Same ranking as chain() and startup_pick()."""
        return [n for n in self._dynamic_order(ctx)
                if n in self._providers and self._providers[n].is_available(ctx)]

    def startup_pick(self, ctx, probe=None) -> str:
        """The one backend to pin for this process, or "" if none can serve.

        Same priority as chain() — a keyed backend outranks the keyless ones —
        with one addition: SearXNG must actually ANSWER before it is chosen.
        chain() can afford to list a dead instance because it falls through at
        call time, but a *pin* has no fallback, so pinning a stopped SearXNG
        would mean no web search at all.
        """
        probe = probe_searxng if probe is None else probe
        for name in self._dynamic_order(ctx):
            provider = self._providers.get(name)
            if provider is None or not provider.is_available(ctx):
                continue
            if name == "searxng" and not probe(ctx):
                continue
            return name
        return ""


# ---------------------------------------------------------------------------
# Rendering and the fallback chain
# ---------------------------------------------------------------------------
# A ddgs answer is "weak" below this many results (or the limit, if smaller).
WEAK_MIN_RESULTS = 3
# ...or when this share of the results comes from one domain.
WEAK_DOMAIN_SHARE = 0.8


def _domain(url: str) -> str:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def assess_results(results: list, limit: int) -> str:
    """Why a result set is too thin to trust as-is, or "" when it is fine.

    Applied to the keyless scraper only (providers with check_quality=True):
    a SearXNG/keyed answer with two hits is an answer, a ddgs answer with two
    hits is usually a throttled or half-failed scrape.
    """
    if not results:
        return "no results"
    floor = max(1, min(WEAK_MIN_RESULTS, limit))
    if len(results) < floor:
        return f"only {len(results)} result{'s' if len(results) != 1 else ''}"
    if len(results) >= WEAK_MIN_RESULTS:
        domains = [_domain(r.url) for r in results]
        top = max(set(domains), key=domains.count)
        if top and domains.count(top) / len(domains) >= WEAK_DOMAIN_SHARE:
            return f"{domains.count(top)} of {len(domains)} results from {top}"
    return ""


def accepts_images(provider) -> bool:
    """Does provider.search take an ``images`` argument?

    Read from the signature rather than by calling and catching TypeError: that
    catch also swallowed a TypeError raised INSIDE a provider and then ran the
    search a second time, and it hid that the images request was dropped.
    """
    try:
        params = inspect.signature(provider.search).parameters
    except (TypeError, ValueError):
        return False
    return "images" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def format_results(success: SearchSuccess, ctx: SearchContext, notes=()) -> str:
    """Render results as compact text, wrapped as untrusted external content.

    The serving provider is always named: a silent fallback would hide a
    broken primary, so "via ddgs" when SearXNG was expected is visible.
    `notes` (and success.notes) are trusted remarks rendered AFTER the
    untrusted wrapper, one ``[note: ...]`` line each.
    """
    all_notes = [n for n in (*success.notes, *notes) if n]
    tail = "".join(f"\n[note: {n}]" for n in all_notes)
    if not success.results:
        return f"No results from {success.provider}.{tail}"
    lines = [f"Search results (via {success.provider}):", ""]
    for index, result in enumerate(success.results, 1):
        lines.append(f"{index}. {result.title}")
        lines.append(f"   {result.url}")
        if result.snippet:
            lines.append(f"   {result.snippet}")
    return ctx.wrap("\n".join(lines), source=f"web_search:{success.provider}") + tail


def run_search(query: str, limit: int, registry: Registry, config: dict,
               ctx: SearchContext, *, return_failures: bool = False,
               images: bool = False, chain=None, return_report: bool = False):
    """Search the configured chain, optionally returning retryable failures.

    The opt-in failure list lets engine.py distinguish a local SearXNG outage
    from a policy denial without parsing text that may be shown to the model.
    images=True asks image-capable backends for image results (SearXNG's
    images category); a backend without image support is called without it
    and the result says so.

    ``chain`` overrides registry.chain() — the engine passes the auto fallback
    order here. ``return_report=True`` returns a SearchReport instead (wins
    over return_failures).

    Weak ddgs answers (assess_results) are retried with another engine group,
    then the rest of the chain is tried; the best weak answer is returned only
    when nothing better turned up, with a note naming what was tried.
    """
    failed_providers = []
    failures = []
    tried = []

    def finish(message: str, provider: str = "", weak: str = ""):
        if return_report:
            return SearchReport(text=message, provider=provider, tried=tuple(tried),
                                failed=tuple(failed_providers),
                                failures=tuple(failures), weak=weak)
        return (message, tuple(failed_providers)) if return_failures else message

    query = (query or "").strip()
    if not query:
        return finish("Error: web_search requires a non-empty 'query'.")

    chain = list(chain) if chain is not None else registry.chain(config, ctx)
    if not chain:
        configured = str(config.get("web_search_provider") or "").strip()
        if configured:
            return finish(f"web_search_provider={configured} is not a known provider. "
                          f"Known: {', '.join(PREFERENCE)}.")
        hints = "\n".join(f"  - {p.setup_hint()}" for p in registry.all())
        return finish("No web search provider is configured. Run `/search setup` to "
                      f"provision a local SearXNG, or enable one of:\n{hints}")

    best = None          # (SearchSuccess, weak reason, image note)
    for provider in chain:
        tried.append(provider.name)
        takes_images = accepts_images(provider)
        image_note = (f"images not supported by {provider.name}; these are web results"
                      if images and not takes_images else "")
        if takes_images:
            outcome = provider.search(query, limit, ctx, images=images)
        else:
            outcome = provider.search(query, limit, ctx)
        if isinstance(outcome, SearchFailure):
            if not outcome.retryable:
                return finish(outcome.error)
            failed_providers.append(provider.name)
            failures.append(f"{provider.name}: {outcome.error}")
            continue
        if not isinstance(outcome, SearchSuccess):
            failed_providers.append(provider.name)
            failures.append(f"{provider.name}: no results")
            continue
        weak = assess_results(outcome.results, limit) if getattr(
            provider, "check_quality", False) else ("" if outcome.results else "no results")
        if weak and hasattr(provider, "search_alternate"):
            retry = provider.search_alternate(query, limit, ctx, outcome)
            if isinstance(retry, SearchSuccess):
                outcome = retry
                weak = assess_results(outcome.results, limit)
        if not weak:
            return finish(_render(outcome, ctx, tried, failures, image_note),
                          provider=provider.name)
        if not outcome.results:
            failed_providers.append(provider.name)
        failures.append(f"{provider.name}: {weak}")
        if outcome.results and (best is None or len(outcome.results) > len(best[0].results)):
            best = (outcome, weak, image_note)

    if best is not None:
        outcome, weak, image_note = best
        note = (f"results may be incomplete ({weak}); tried: {', '.join(tried)}")
        return finish(_render(outcome, ctx, (), (), image_note, extra=note),
                      provider=outcome.provider, weak=weak)
    if failures and all(f.endswith(": no results") for f in failures):
        return finish(f"No results found (tried: {', '.join(tried)}). Try a broader "
                      "or differently worded query.")
    return finish("Every configured web search provider failed:\n"
                  + "\n".join(f"  - {f}" for f in failures)
                  + "\nRun `/search doctor` to diagnose.")


def _render(outcome, ctx, tried, failures, image_note, extra=""):
    """format_results plus the fallback notes for this call."""
    notes = []
    if failures:
        notes.append("fell back after " + "; ".join(failures)
                     + f" — served by {outcome.provider}")
    if image_note:
        notes.append(image_note)
    if extra:
        notes.append(extra)
    return format_results(outcome, ctx, notes=notes)


# ---------------------------------------------------------------------------
# SearXNG — the default backend
# ---------------------------------------------------------------------------
class SearxngProvider(WebSearchProvider):
    """Self-hosted SearXNG via its JSON API.

    Reads the existing ``search_base_url`` key so installs that configured
    SearXNG before this registry existed keep working untouched.
    """

    @property
    def name(self):
        return "searxng"

    def setup_schema(self):
        return {"name": "SearXNG", "badge": "default · free · self-hosted",
                "tag": "Meta-search over 70+ engines, no API key, queries stay local",
                "env_vars": [], "post_setup": "searxng_container"}

    def _base(self, ctx):
        return str(ctx.config.get("search_base_url") or "").strip()

    def is_available(self, ctx):
        return bool(self._base(ctx))

    def search(self, query, limit, ctx, images=False):
        base = self._base(ctx)
        if not base:
            return SearchFailure("search_base_url is not set")
        # An image hunt ("download me the picture of X", images=true) wants
        # actual image URLs, not article pages about X -- SearXNG's images
        # category returns exactly that. The engine also sets this when the
        # model passes images=true, so a reformulated keyword-only query
        # still routes right.
        lowered = query.lower()
        wants_images = images or any(w in lowered for w in (
            "picture", "photo", "image", "pictures", "photos", "images", "logo",
            "wallpaper", "screenshot"))
        category = "&categories=images" if wants_images else ""
        url = f"{base}{urllib.parse.quote(query)}{category}&format=json"

        parts = urllib.parse.urlparse(url)
        # Plaintext HTTP is fine to a box you control; over the internet it puts
        # every query on the wire. Same rule OpenClaw's SearXNG plugin enforces.
        if parts.scheme == "http" and not _is_local_host(parts.hostname or ""):
            return SearchFailure(
                f"Refusing plaintext http:// to public host '{parts.hostname}' — "
                "use https:// for a remote SearXNG instance.", retryable=False)

        blocked = ctx.check_url(url)
        if blocked:
            return SearchFailure(blocked, retryable=False)

        try:
            payload = _http_get_json(url, timeout=HTTP_TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                return SearchFailure(
                    f"SearXNG returned HTTP {exc.code} — the bot limiter is likely on. "
                    "Set server.limiter: false in settings.yml for local API use.")
            return SearchFailure(f"SearXNG returned HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return SearchFailure(f"Could not reach SearXNG at {parts.netloc}: {exc}")
        except (ValueError, _json.JSONDecodeError):
            # The endpoint served HTML. This is the single most common
            # misconfiguration, so it gets its own message.
            return SearchFailure(
                "SearXNG did not return JSON. Add `json` to search.formats in "
                "settings.yml (JSON output is disabled by default upstream).")

        raw = payload.get("results") or [] if isinstance(payload, dict) else []
        ranked = sorted(raw, key=lambda r: float(r.get("score") or 0), reverse=True)
        results = [SearchResult(title=str(r.get("title") or ""),
                                url=str(r.get("url") or ""),
                                snippet=str(r.get("content") or "")[:MAX_SNIPPET_CHARS])
                   for r in ranked[:limit] if r.get("url")]
        return SearchSuccess(results, provider=self.name)


def _probe_timeout(base_url: str) -> int:
    """Budget for probing `base_url` — see STARTUP_PROBE_TIMEOUT.

    Deliberately does not resolve the host: this is on the startup path, and a
    DNS round-trip to classify the endpoint would cost the very milliseconds the
    short remote budget exists to protect. A name that resolves to loopback but
    is not spelled like one simply gets the conservative budget.
    """
    try:
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    except ValueError:
        return STARTUP_PROBE_TIMEOUT
    if host == "localhost":
        return LOCAL_PROBE_TIMEOUT
    try:
        return (LOCAL_PROBE_TIMEOUT if ipaddress.ip_address(host).is_loopback
                else STARTUP_PROBE_TIMEOUT)
    except ValueError:  # not an IP literal — a real name, treat as remote
        return STARTUP_PROBE_TIMEOUT


def probe_searxng(ctx: SearchContext, timeout: int = None) -> bool:
    """Does the configured SearXNG answer the JSON API right now?

    Startup-only, called by Registry.startup_pick. Deliberately NOT wired into
    SearxngProvider.is_available, which must stay network-free — a health ping
    on every search would double latency and still race.

    Goes through ctx.check_url like any other outbound request: the probe is a
    real network call and must not be the one request that skips egress/SSRF.
    """
    base = str(ctx.config.get("search_base_url") or "").strip()
    if not base:
        return False
    url = f"{base}{urllib.parse.quote('agent8088-startup-probe')}&format=json"
    if ctx.check_url(url):
        return False
    if timeout is None:
        timeout = _probe_timeout(base)
    try:
        payload = _http_get_json(url, timeout=timeout)
        return isinstance(payload, dict) and isinstance(payload.get("results"), list)
    except Exception:  # noqa: BLE001 — any failure means "not usable right now"
        return False


# ---------------------------------------------------------------------------
# ddgs — the bundled keyless fallback
# ---------------------------------------------------------------------------
# Engine -> every host that engine reaches. Derived from the INSTALLED ddgs
# package, not from documentation. Three things a guess gets wrong:
# google/bing/yandex are not registered text engines in 9.x; yahoo also
# reaches www.bing.com; the previous
# lite.duckduckgo.com entry is stale).
#
# This map is what the egress policy is enforced against, and the enforcement fails
# CLOSED, so a missing host is a silent policy bypass and a wrong host a false
# denial. Re-derive it whenever the ddgs pin moves.
_DDGS_ENGINE_HOSTS = {
    "duckduckgo": ("https://html.duckduckgo.com", "https://duckduckgo.com"),
    "brave":      ("https://search.brave.com",),
    "mojeek":     ("https://www.mojeek.com",),
    "startpage":  ("https://www.startpage.com",),
    # yahoo proxies bing, so it reaches both. Blocking either must drop the engine.
    "yahoo":      ("https://search.yahoo.com", "https://www.bing.com"),
    # Templated upstream as https://{lang}.wikipedia.org/... — pinned to en by
    # _ddgs_text passing region=us-en, which is what keeps this entry truthful.
    "wikipedia":  ("https://en.wikipedia.org",),
}

# Rotation order handed to ddgs: general-purpose engines first, encyclopaedic last.
#
# The substantive reason to name engines at all is throttle rotation: each engine is
# a separate rate-limit bucket, so a 202 from one moves to the next instead of
# ending the search. With backend unset the library picks for us and a throttle has
# nowhere to go.
#
# The ordering itself is a secondary, weaker argument, and worth stating precisely
# so nobody over-trusts it. Upstream DOCUMENTS "auto" as prioritising Wikipedia and
# Grokipedia, which would be poor for the queries web_search exists for ("current
# leaders, releases, prices, availability, schedules, news"; see tools.txt).
# Measured against ddgs 9.14.4, "auto" was less encyclopaedic than that description
# implies — a news-shaped query returned Wikipedia first but then ESPN and NBC — and
# this explicit order returned Wikipedia first for the same query too. So treat the
# ordering as making intent explicit and stable across ddgs versions, not as a
# measured retrieval win.
#
# grokipedia is deliberately absent: its endpoint is /api/typeahead, an autocomplete
# API rather than a web search, so it would widen the egress surface for almost no
# retrieval value.
_DDGS_ENGINE_ORDER = ("duckduckgo", "brave", "mojeek", "startpage", "yahoo", "wikipedia")

# The union, kept under the original name so the module docstring's egress claim
# and any embedder referencing it still see every host ddgs can reach.
_DDGS_HOSTS = tuple(h for e in _DDGS_ENGINE_ORDER for h in _DDGS_ENGINE_HOSTS[e])

# Region is pinned rather than left to the library default so the wikipedia host
# above stays correct by construction. A non-en region would send the search to
# <lang>.wikipedia.org — a host the allowlist never checked.
_DDGS_REGION = "us-en"

# Per-request timeout handed to DDGS(). The library default is 5s, which sits under
# a slow engine's ordinary response time and turns a working search into a
# TimeoutException.
_DDGS_TIMEOUT = 10

# Minimum spacing between two ddgs calls in this process.
#
# Hermes' guidance for this backend, verbatim: "DuckDuckGo may throttle after many
# rapid requests. Add a short delay between searches." The cache below only helps
# IDENTICAL queries; an agent working through three DIFFERENT searches in a loop
# still spends three times from the same sliding window. Costs nothing when
# searches are naturally more than a couple of seconds apart, which is the common
# case since the model has to read each result set first.
_DDGS_MIN_INTERVAL = 2.0
_ddgs_last_call = 0.0

# A search that starts this soon after the previous ddgs call is part of a
# burst. Mid-burst, "no results found" usually means a throttled engine served
# nothing rather than that the topic is empty: live, two of ten back-to-back
# city lookups ("current mayor of Vienna 2026") came back empty that way.
_DDGS_BURST_WINDOW = 20.0

# Attempts per search. One retry is not padding: a 202 is issued per-engine
# per-IP on a sliding window, so a later attempt lands elsewhere in the rotation
# and usually serves. Hermes: "Wait a few seconds and retry."
_DDGS_ATTEMPTS = 3
_DDGS_BACKOFF = (0.0, 1.5, 4.0)

# Short-TTL result cache, keyed on (query, limit).
#
# The reported symptom was that the SECOND search rate-limits. Agent loops re-issue
# near-identical queries within seconds — a retry after a failed browse_page, a
# re-read of the same tool result — and every one spends from the same window.
# Serving a repeat from cache costs a dict lookup and removes the request that
# would have been throttled. Deliberately tiny and process-local: this is throttle
# relief, not a search index.
_DDGS_CACHE_TTL = 300
_DDGS_CACHE_MAX = 32
_ddgs_cache: dict = {}


def _ddgs_cache_get(query: str, limit: int):
    entry = _ddgs_cache.get((query, limit))
    if entry is None:
        return None
    stored_at, results = entry
    if time.monotonic() - stored_at >= _DDGS_CACHE_TTL:
        _ddgs_cache.pop((query, limit), None)
        return None
    return results


def _ddgs_cache_put(query: str, limit: int, results: list) -> None:
    if len(_ddgs_cache) >= _DDGS_CACHE_MAX:
        # Evict the oldest. Insertion-ordered dicts make this scan-free, and at 32
        # entries an exact LRU would not pay for itself.
        _ddgs_cache.pop(next(iter(_ddgs_cache)), None)
    _ddgs_cache[(query, limit)] = (time.monotonic(), results)


def _ddgs_wait_turn() -> None:
    """Keep consecutive ddgs calls at least _DDGS_MIN_INTERVAL apart."""
    global _ddgs_last_call
    if _ddgs_last_call:
        gap = time.monotonic() - _ddgs_last_call
        if gap < _DDGS_MIN_INTERVAL:
            time.sleep(_DDGS_MIN_INTERVAL - gap)
    _ddgs_last_call = time.monotonic()


def _ddgs_throttle_errors() -> tuple:
    """Exception types that mean "try again", or () if upstream renamed them.

    Imported by type rather than matched by string. The previous check was
    `"ratelimit" in msg or "202" in msg`, and that second arm matches any message
    containing those three digits — a year, a byte count, an HTTP 202 that is not a
    throttle — so unrelated failures were reported to the user as rate limits, with
    rate-limit advice attached. Returning () rather than raising keeps the generic
    handler in charge if the module layout changes under the >=9,<10 pin.
    """
    try:
        from ddgs.exceptions import RatelimitException, TimeoutException
        return (RatelimitException, TimeoutException)
    except Exception:  # noqa: BLE001
        return ()


# Cached tri-state: None = not probed yet, True/False = the answer.
_ddgs_import_state = None


def _ddgs_installed() -> bool:
    """Is ddgs actually importable right now?

    find_spec() was the wrong test, and is why detection was reported as working on
    some machines and not others: it answers "is there a module of this name on
    sys.path", which stays True for a distribution whose Python files landed but
    whose compiled dependency did not. ddgs pulls in a native extension, and a
    wheel-less interpreter version, a partially rolled-back install, or an ABI
    mismatch all leave ddgs/ present with `import ddgs` raising — find_spec says
    yes, /search doctor prints "ddgs importable: yes", and the search then dies on
    the very ImportError this check exists to prevent.

    So import the symbol the provider actually calls. find_spec stays as a cheap
    pre-filter, to avoid paying for an import attempt in the common
    genuinely-absent case. Cached because is_available() runs on every search and
    the doctor table calls it again.
    """
    global _ddgs_import_state
    if _ddgs_import_state is None:
        if importlib.util.find_spec("ddgs") is None:
            _ddgs_import_state = False
        else:
            try:
                from ddgs import DDGS  # noqa: F401
                _ddgs_import_state = True
            except Exception:  # noqa: BLE001 — ImportError, OSError from a bad .so, anything
                _ddgs_import_state = False
    return _ddgs_import_state


def _ddgs_text(query: str, limit: int, *, backend: str, timeout: int,
               proxy=None, region: str = _DDGS_REGION):
    """Call the ddgs library. Isolated so tests can patch it without importing the
    package.

    Two details here are Hermes' documented requirements rather than preference:
    DDGS is used as a CONTEXT MANAGER so its HTTP client and connections are closed
    (the previous code built a bare DDGS() per search and never closed it, leaking
    a client per call), and max_results is passed as a KEYWORD argument.
    """
    from ddgs import DDGS

    with DDGS(timeout=timeout, proxy=proxy) as ddgs:
        return ddgs.text(query, max_results=limit, backend=backend, region=region)


def _ddgs_allowed_engines(ctx) -> tuple:
    """Engines whose COMPLETE host set passes the egress policy, plus the first
    rejection reason seen.

    Per-engine rather than all-or-nothing. The previous check tested three hosts and
    refused the whole backend if any one was blocked; widening the engine list under
    that rule would have made ddgs MORE likely to be denied — ten hosts to allow
    instead of three — which is the opposite of the intent.

    The security property is unchanged and still fails closed: an engine is only
    offered to the library when EVERY host it reaches is permitted, and the library
    reaches no host this map does not name. yahoo is the case that matters — it
    proxies bing, so blocking either host must drop the whole engine.
    """
    allowed = []
    first_block = ""
    for engine in _DDGS_ENGINE_ORDER:
        blocked = ""
        for host in _DDGS_ENGINE_HOSTS[engine]:
            blocked = ctx.check_url(host)
            if blocked:
                break
        if blocked:
            first_block = first_block or blocked
            continue
        allowed.append(engine)
    return allowed, first_block


def _ddgs_proxy(ctx) -> str:
    """Optional outbound proxy for ddgs, from the ``search_proxy`` config key.

    Hermes notes that DuckDuckGo blocks some cloud IPs, which today leaves a VPS
    install with no recourse at all. A proxy is itself an egress destination, so it
    goes through ctx.check_url like any other URL — a proxy the policy forbids is
    dropped rather than honoured, which is the safe direction to fail.
    """
    proxy = str(ctx.config.get("search_proxy") or "").strip()
    if not proxy:
        return ""
    return "" if ctx.check_url(proxy) else proxy


class DdgsProvider(WebSearchProvider):
    """Keyless metasearch via the bundled ddgs package.

    ddgs scrapes result pages rather than using an API, so it is still the backend
    most likely to throttle under sustained agent use — but it no longer gives up
    when it does. Four things carry that: several engines are named explicitly so a
    202 rotates to the next throttle bucket instead of ending the search, EVERY
    attempt failure is retried with backoff (not just recognised throttle
    exceptions — a connection reset or an unwrapped timeout gets the same second
    chance), repeat queries are served from a short-lived cache, and — because it
    is the fallback nobody has to opt into — engine.py exempts it from the
    permission-escalation prompt when it is the only backend in the chain (see
    engine._ddgs_only_chain), so a missing/misconfigured SearXNG or API key can
    never turn into "web_search does not work at all". It earns its place as the
    fallback that needs no key, no hosting and no setup, and because it ships as a
    dependency web_search always has somewhere to land when SearXNG is absent or
    failing.

    SECURITY: the library owns its HTTP client, so its requests do NOT pass through
    engine.py's guard. Every host each engine reaches is therefore checked against
    the policy BEFORE the library is called, and the check FAILS CLOSED — refusing
    rather than silently bypassing an operator's egress allowlist. The denial is
    non-retryable so run_search does not shop for another backend to route around
    the decision. See _ddgs_allowed_engines for why the check is per-engine.
    """

    # run_search quality-checks this backend's answers (assess_results) and
    # calls search_alternate() before accepting a weak one.
    check_quality = True

    @property
    def name(self):
        return "ddgs"

    def setup_schema(self):
        return {"name": "DuckDuckGo (ddgs)",
                "badge": "fallback · free · no key · bundled",
                "tag": "Keyless metasearch, no hosting, no setup — ships with agent8088",
                "env_vars": [], "post_setup": ""}

    def is_available(self, ctx):
        # Ships as a dependency, so this is normally True. Still checked rather
        # than assumed: a stripped or partially-installed environment should
        # report "unavailable" instead of raising ImportError mid-search.
        return _ddgs_installed()

    def search(self, query, limit, ctx):
        if not _ddgs_installed():
            return SearchFailure(
                "ddgs is not importable (it ships with agent8088) — reinstall the "
                "package or configure another backend. Check with `/search doctor`.")

        engines, blocked = _ddgs_allowed_engines(ctx)
        if not engines:
            return SearchFailure(
                f"ddgs cannot run under the current egress policy ({blocked})",
                retryable=False)
        backend = ",".join(engines)

        # Before the throttle wait, deliberately: a cache hit must cost nothing.
        cached = _ddgs_cache_get(query, limit)
        if cached is not None:
            return SearchSuccess(cached, provider=self.name)

        proxy = _ddgs_proxy(ctx) or None
        throttles = _ddgs_throttle_errors()
        last_error = ""
        throttled = False
        in_burst = bool(_ddgs_last_call) and (
            time.monotonic() - _ddgs_last_call < _DDGS_BURST_WINDOW)

        for attempt in range(_DDGS_ATTEMPTS):
            if _DDGS_BACKOFF[attempt]:
                time.sleep(_DDGS_BACKOFF[attempt])
            _ddgs_wait_turn()
            try:
                raw = _ddgs_text(query, limit, backend=backend,
                                 timeout=_DDGS_TIMEOUT, proxy=proxy) or []
            except Exception as exc:  # noqa: BLE001 — a provider must never raise
                message = str(exc) or exc.__class__.__name__
                # Upstream signals "zero hits" by raising. That is not a provider
                # failure, and reporting it as one would send run_search shopping
                # for another backend over a query nothing can answer.
                if "no results found" in message.lower():
                    if in_burst and not (throttled or last_error):
                        # Possibly throttled: spend the backed-off attempts
                        # before calling it empty. Every attempt empty is a
                        # real "no results".
                        if attempt + 1 < _DDGS_ATTEMPTS:
                            continue
                        return SearchSuccess([], provider=self.name)
                    if not (throttled or last_error):
                        return SearchSuccess([], provider=self.name)
                    # ...unless earlier attempts were throttled or failed: then
                    # "nothing" most likely means "nothing served", and saying
                    # "no results found" would tell the model the topic is empty.
                    return SearchFailure(
                        f"ddgs returned nothing after {attempt} failed attempt"
                        f"{'s' if attempt != 1 else ''} ({last_error}) — search may "
                        "be throttled, not empty. Retry shortly, or configure "
                        "SearXNG or an API-key backend (`/search setup`).")
                last_error = message
                if throttles and isinstance(exc, throttles):
                    throttled = True
                # Retry everything else too, not just recognised throttle types:
                # a connection reset, a timeout ddgs did not wrap, or one engine's
                # page layout changing are attempt-specific, not query-specific —
                # and `backend` names several engines (separate throttle/failure
                # buckets), so a later attempt is not just "try the same thing
                # again". Only running out of attempts ends the search.
                continue
            results = [SearchResult(title=str(r.get("title") or ""),
                                    url=str(r.get("href") or ""),
                                    snippet=str(r.get("body") or "")[:MAX_SNIPPET_CHARS])
                       for r in list(raw)[:limit] if r.get("href")]
            # Only cache a real answer: caching "nothing" for five minutes would
            # hide a transient failure behind a fast empty reply.
            if results:
                _ddgs_cache_put(query, limit, results)
            return SearchSuccess(results, provider=self.name)

        if throttled:
            return SearchFailure(
                f"ddgs is rate limited across {len(engines)} engines after "
                f"{_DDGS_ATTEMPTS} attempts. Configure SearXNG or an API-key "
                "backend for sustained use — run `/search setup`.")
        return SearchFailure(f"ddgs search failed: {last_error}")

    def search_alternate(self, query, limit, ctx, first):
        """One more pass with the engine rotation started halfway round.

        ddgs stops at the first engines that fill max_results, so a thin answer
        came from the head of the rotation; starting from the other half asks
        different throttle buckets and different indexes. Merged with `first`
        (deduplicated by URL). Returns None when there is nothing else to try or
        the pass fails — run_search then keeps the first answer. Never raises.
        """
        engines, _blocked = _ddgs_allowed_engines(ctx)
        if len(engines) < 2:
            return None
        split = max(1, len(engines) // 2)
        rotated = engines[split:] + engines[:split]
        _ddgs_wait_turn()
        try:
            raw = _ddgs_text(query, limit, backend=",".join(rotated),
                             timeout=_DDGS_TIMEOUT, proxy=_ddgs_proxy(ctx) or None) or []
        except Exception:  # noqa: BLE001 — a provider must never raise
            return None
        seen = {r.url for r in first.results}
        merged = list(first.results)
        for r in list(raw):
            url = str(r.get("href") or "")
            if url and url not in seen and len(merged) < limit:
                seen.add(url)
                merged.append(SearchResult(title=str(r.get("title") or ""), url=url,
                                           snippet=str(r.get("body") or "")[:MAX_SNIPPET_CHARS]))
        if merged:
            _ddgs_cache_put(query, limit, merged)
        return SearchSuccess(merged, provider=self.name,
                             notes=[*first.notes,
                                    f"ddgs retried with engines starting at {rotated[0]}"])


# ---------------------------------------------------------------------------
# Optional API-key backends
# ---------------------------------------------------------------------------
class _KeyedProvider(WebSearchProvider):
    """Shared plumbing for the OPTIONAL API-key backends.

    Optional means exactly one thing: is_available() is False until the key is
    present, so the backend never enters the chain for a user who did not opt
    in — and needs no removal or disabling to stay out of the way.

    Each subclass only ever receives its OWN key. engine.py's
    _outbound_secret_check floor still runs per request via ctx.check_url, so a
    credential cannot be posted to another vendor's host.
    """
    env_var = ""
    endpoint = ""
    label = ""
    signup_url = ""
    blurb = ""

    def setup_schema(self):
        return {"name": self.label, "badge": "optional · API key",
                "tag": self.blurb,
                "env_vars": [{"key": self.env_var,
                              "prompt": f"{self.label} API key",
                              "url": self.signup_url}],
                "post_setup": ""}

    def is_available(self, ctx):
        return bool(ctx.get_secret(self.env_var))

    def _request(self, query, limit, key):
        raise NotImplementedError

    def _parse(self, payload):
        raise NotImplementedError

    def search(self, query, limit, ctx):
        key = ctx.get_secret(self.env_var)
        if not key:
            return SearchFailure(f"{self.env_var} is not set")
        blocked = ctx.check_url(self.endpoint)
        if blocked:
            return SearchFailure(blocked, retryable=False)
        for attempt in range(KEYED_ATTEMPTS):
            try:
                payload = _http_json(**self._request(query, limit, key))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    return SearchFailure(
                        f"{self.label} rejected the credential — check {self.env_var}.",
                        retryable=False)
                transient = exc.code == 429 or exc.code >= 500
                if transient and attempt + 1 < KEYED_ATTEMPTS:
                    # One retry, honouring Retry-After but never stalling a turn
                    # for long: past the cap, falling to the next backend is better.
                    time.sleep(_retry_after_seconds(exc))
                    continue
                if exc.code == 429:
                    return SearchFailure(f"{self.label} rate limit reached (HTTP 429).")
                return SearchFailure(f"{self.label} returned HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                return SearchFailure(f"Could not reach {self.label}: {exc}")
            except (ValueError, _json.JSONDecodeError):
                return SearchFailure(f"{self.label} returned a malformed response")
            return SearchSuccess(self._parse(payload)[:limit], provider=self.name)
        return SearchFailure(f"{self.label} did not answer")  # unreachable in practice


# Keyed backends: one retry on 429/5xx, waiting Retry-After up to this cap.
KEYED_ATTEMPTS = 2
KEYED_RETRY_CAP = 5.0
KEYED_RETRY_DEFAULT = 1.0


def _retry_after_seconds(exc) -> float:
    """Retry-After in seconds (delta form only), clamped to KEYED_RETRY_CAP."""
    try:
        value = float((exc.headers or {}).get("Retry-After") or KEYED_RETRY_DEFAULT)
    except (TypeError, ValueError, AttributeError):
        value = KEYED_RETRY_DEFAULT
    return max(0.0, min(value, KEYED_RETRY_CAP))


class TavilyProvider(_KeyedProvider):
    env_var = "TAVILY_API_KEY"
    endpoint = "https://api.tavily.com/search"
    label = "Tavily"
    signup_url = "https://tavily.com"
    blurb = "Agent-optimized results with citations"

    @property
    def name(self):
        return "tavily"

    def _request(self, query, limit, key):
        return {"url": self.endpoint, "method": "POST",
                "headers": {"Content-Type": "application/json",
                            "Authorization": f"Bearer {key}"},
                "body": {"query": query, "max_results": limit}}

    def _parse(self, payload):
        return [SearchResult(str(r.get("title") or ""), str(r.get("url") or ""),
                             str(r.get("content") or "")[:MAX_SNIPPET_CHARS])
                for r in (payload.get("results") or []) if r.get("url")]


class ExaProvider(_KeyedProvider):
    env_var = "EXA_API_KEY"
    endpoint = "https://api.exa.ai/search"
    label = "Exa"
    signup_url = "https://exa.ai"
    blurb = "Semantic/neural search — finds pages by meaning"

    @property
    def name(self):
        return "exa"

    def _request(self, query, limit, key):
        return {"url": self.endpoint, "method": "POST",
                "headers": {"Content-Type": "application/json", "x-api-key": key},
                "body": {"query": query, "numResults": limit,
                         "contents": {"text": {"maxCharacters": MAX_SNIPPET_CHARS}}}}

    def _parse(self, payload):
        return [SearchResult(str(r.get("title") or ""), str(r.get("url") or ""),
                             str(r.get("text") or "")[:MAX_SNIPPET_CHARS])
                for r in (payload.get("results") or []) if r.get("url")]


def default_registry() -> Registry:
    """All four backends. Order here is irrelevant — PREFERENCE decides."""
    return Registry([SearxngProvider(), TavilyProvider(), ExaProvider(),
                     DdgsProvider()])
