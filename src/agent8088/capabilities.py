"""Degradation registry: which capabilities are running on a fallback, and why.

Every subsystem that can quietly fall back (web search to ddgs, memory to
keyword-only recall, a sandbox to Docker, ...) reports its current state here.
Everything that shows the user or the model "what is limited right now" reads
from here instead of re-deriving it: the startup banner, mid-session notices,
/status, /doctor (and --doctor, /api/doctor), /capabilities, /api/status and
the one-line caveats appended to tool results.

Reporting is cheap and idempotent — call report() every time you know the
state, not only when it changes. Only a *state* change (ok -> degraded,
degraded -> ok, ...) is announced:

  * it is logged once to the ``agent8088.degraded`` logger (WARNING when
    something got worse, INFO on recovery). logging_setup routes that logger
    to the JSONL file always, and to stderr only when nobody subscribed —
    see logging_setup.DegradedConsoleHandler;
  * every subscriber is called with a Change. The CLI subscribes and prints a
    dim one-line notice at a safe point (after a tool result, before the next
    prompt), so nothing writes over a spinner or a live render.

This module imports nothing from agent8088, so any subsystem (including
web_search.py, which must not import engine.py) can use it.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

# Stable capability names. Subsystems report under these; UIs key on them.
SEARCH = "search"
MEMORY = "memory"
MEMORY_EMBED = "memory_embed"
SANDBOX = "sandbox"
BROWSER = "browser"
MODEL = "model"
CONTEXT = "context"
MCP = "mcp"
OCR = "ocr"
DOCUMENTS = "documents"
GATEWAY = "gateway"
INSTALL = "install"

NAMES = (SEARCH, MEMORY, MEMORY_EMBED, SANDBOX, BROWSER, MODEL, CONTEXT, MCP,
         OCR, DOCUMENTS, GATEWAY, INSTALL)

# Human labels for tables and notices. Unknown names fall back to the name.
LABELS = {
    SEARCH: "search", MEMORY: "memory", MEMORY_EMBED: "semantic recall",
    SANDBOX: "sandbox", BROWSER: "browser", MODEL: "model", CONTEXT: "context",
    MCP: "MCP", OCR: "OCR", DOCUMENTS: "documents", GATEWAY: "gateway",
    INSTALL: "install",
}

# Capabilities the agent cannot work without. /doctor reports these as "fail"
# when unavailable; everything else is optional and only ever "warn".
CORE = frozenset({MODEL})

OK = "ok"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"
STATES = (OK, DEGRADED, UNAVAILABLE)
_SEVERITY = {OK: 0, DEGRADED: 1, UNAVAILABLE: 2}

LOGGER_NAME = "agent8088.degraded"
_log = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class Capability:
    """One capability's current state. Immutable; report() replaces it."""
    name: str
    active: str            # what is actually serving ("ddgs", "keyword-only")
    preferred: str         # what would serve when healthy ("" if n/a)
    state: str             # ok | degraded | unavailable
    reason: str            # why it is not the preferred one
    impact: str            # what the user loses
    fix: str               # command or edit that upgrades it
    model_note: str        # one-line caveat for tool results ("" = default)
    since: float           # epoch seconds of the last *state* change
    updated: float         # epoch seconds of the last report()

    @property
    def label(self) -> str:
        return LABELS.get(self.name, self.name)

    @property
    def ok(self) -> bool:
        return self.state == OK

    def as_dict(self) -> dict:
        data = asdict(self)
        data["label"] = self.label
        return data


@dataclass(frozen=True)
class Change:
    """Handed to subscribers when a capability's state changes."""
    name: str
    old_state: str         # OK for a capability reported for the first time
    new_state: str
    previous_active: str
    entry: Capability

    @property
    def recovered(self) -> bool:
        return _SEVERITY[self.new_state] < _SEVERITY[self.old_state]

    @property
    def message(self) -> str:
        return describe_change(self)


_lock = threading.RLock()
_entries: dict[str, Capability] = {}
_subscribers: list[Callable[[Change], None]] = []


def report(name: str, *, active: str, preferred: str | None = None,
           state: str = OK, reason: str = "", impact: str = "", fix: str = "",
           model_note: str = "") -> bool:
    """Record a capability's current state; return True if the state changed.

    Idempotent: repeating the same report changes nothing and notifies no one.
    Other fields (reason, active, ...) are refreshed on every call, but only a
    change of `state` resets `since`, logs, and notifies subscribers. A first
    report counts as a change only when it is not ok — "it works" is not news.
    """
    if state not in STATES:
        raise ValueError(f"unknown capability state {state!r}; expected one of {STATES}")
    now = time.time()
    with _lock:
        old = _entries.get(name)
        old_state = old.state if old else OK
        changed = old_state != state
        since = now if (changed or old is None) else old.since
        entry = Capability(
            name=name, active=str(active or ""),
            preferred=str(preferred if preferred is not None
                          else (old.preferred if old else "")),
            state=state, reason=str(reason or ""), impact=str(impact or ""),
            fix=str(fix or ""), model_note=str(model_note or ""),
            since=since, updated=now)
        _entries[name] = entry
        subscribers = list(_subscribers)
    if not changed:
        return False
    change = Change(name=name, old_state=old_state, new_state=state,
                    previous_active=old.active if old else "", entry=entry)
    _announce(change, subscribers)
    return True


def _announce(change: Change, subscribers) -> None:
    """Log once, then notify. Never raises into the reporting subsystem."""
    try:
        level = logging.INFO if change.recovered else logging.WARNING
        # `console` tells logging_setup.DegradedConsoleHandler whether to echo
        # this to stderr: only when no UI has subscribed to render it itself.
        _log.log(level, change.message,
                 extra={"degraded": True, "capability": change.name,
                        "console": not subscribers})
    except Exception:  # noqa: BLE001
        pass
    for callback in subscribers:
        try:
            callback(change)
        except Exception:  # noqa: BLE001 — a broken UI hook must not break a search
            pass


def get(name: str) -> Capability | None:
    with _lock:
        return _entries.get(name)


def all() -> list[Capability]:  # noqa: A001 — the registry's natural verb
    """Every reported capability, worst state first, then by name."""
    with _lock:
        entries = list(_entries.values())
    return sorted(entries, key=lambda e: (-_SEVERITY[e.state], e.name))


def degraded() -> list[Capability]:
    """Entries that are not ok (degraded or unavailable), worst first."""
    return [e for e in all() if e.state != OK]


def clear(name: str) -> bool:
    """Forget a capability (e.g. the feature was turned off). Silent."""
    with _lock:
        return _entries.pop(name, None) is not None


def reset() -> None:
    """Forget every entry and subscriber. For tests and embedders."""
    with _lock:
        _entries.clear()
        _subscribers.clear()


def subscribe(callback: Callable[[Change], None]) -> Callable[[], None]:
    """Call `callback(change)` on every state change; returns an unsubscribe.

    Callbacks run on the reporting thread (possibly a worker thread, possibly
    mid-render), so a UI callback should queue and render later rather than
    print. Exceptions from callbacks are swallowed.
    """
    with _lock:
        _subscribers.append(callback)

    def unsubscribe():
        with _lock:
            try:
                _subscribers.remove(callback)
            except ValueError:
                pass
    return unsubscribe


def has_subscribers() -> bool:
    with _lock:
        return bool(_subscribers)


def model_note(name: str) -> str:
    """One-line caveat to append to a tool result, or "" when ok/unknown."""
    entry = get(name)
    if entry is None or entry.state == OK:
        return ""
    if entry.model_note:
        return entry.model_note
    detail = f" — {entry.impact}" if entry.impact else ""
    return f"[note: {entry.label} is {entry.state} ({entry.active or 'none'}){detail}]"


def banner_line() -> str:
    """`⚠ limited: search=ddgs · memory=keyword-only — /doctor`, or ""."""
    items = degraded()
    if not items:
        return ""
    parts = " · ".join(f"{e.name}={e.active if e.state == DEGRADED and e.active else 'off'}"
                       for e in items)
    return f"⚠ limited: {parts} — /doctor"


def rows() -> list[dict]:
    """Every entry as a plain dict (JSON-safe), worst first. For tables/APIs."""
    return [e.as_dict() for e in all()]


def describe_change(change: Change) -> str:
    """The one-line notice for a state change."""
    entry = change.entry
    label = entry.label
    if change.recovered:
        if change.new_state == OK:
            back = f" to {entry.active}" if entry.active else ""
            return f"{label} upgraded back{back}"
        return f"{label} partly recovered: now {entry.active or entry.state}"
    because = f" ({entry.reason})" if entry.reason else ""
    if change.new_state == UNAVAILABLE or not entry.active:
        head = f"{label} unavailable{because}"
    elif change.previous_active and change.previous_active != entry.active:
        head = f"{label} switched to {entry.active}{because}"
    else:  # first report (startup), or limited in place
        head = f"{label} using {entry.active}{because}"
    tail = "; ".join(part for part in (entry.impact, entry.fix) if part)
    return f"{head} — {tail}" if tail else head


def doctor_status(entry: Capability) -> str:
    """Map a capability to a /doctor status: ok | warn | fail."""
    if entry.state == OK:
        return "ok"
    if entry.state == UNAVAILABLE and entry.name in CORE:
        return "fail"
    return "warn"
