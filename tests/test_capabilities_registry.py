"""agent8088.capabilities: the degradation registry and its logging route.

The registry is the single place the banner, /status, /doctor, /capabilities,
/api/status and tool-result caveats read "what is limited right now" from.
"""
import json
import logging
import threading
from types import SimpleNamespace

import pytest

from agent8088 import capabilities as C
from agent8088 import logging_setup as L


def _degrade_search(**overrides):
    fields = dict(active="ddgs", preferred="searxng", state=C.DEGRADED,
                  reason="SearXNG not answering", impact="keyless scraper",
                  fix="/search setup")
    fields.update(overrides)
    return C.report(C.SEARCH, **fields)


# --- report() / get() -----------------------------------------------------------

def test_a_first_ok_report_is_not_a_change():
    seen = []
    C.subscribe(seen.append)
    assert C.report(C.SEARCH, active="searxng") is False
    assert seen == []
    assert C.get(C.SEARCH).state == C.OK


def test_a_first_degraded_report_is_a_change_and_notifies():
    seen = []
    C.subscribe(seen.append)
    assert _degrade_search() is True
    assert len(seen) == 1
    change = seen[0]
    assert (change.name, change.old_state, change.new_state) == (C.SEARCH, C.OK, C.DEGRADED)
    assert change.entry.active == "ddgs"
    assert not change.recovered


def test_repeating_a_report_is_idempotent(monkeypatch):
    seen = []
    C.subscribe(seen.append)
    clock = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr(C, "time", SimpleNamespace(time=lambda: next(clock)))
    _degrade_search()
    assert _degrade_search(reason="still down") is False
    entry = C.get(C.SEARCH)
    assert entry.since == 100.0, "since moves only on a state change"
    assert entry.updated == 200.0
    assert entry.reason == "still down", "other fields refresh on every report"
    assert len(seen) == 1


def test_a_state_change_resets_since(monkeypatch):
    clock = iter([100.0, 200.0])
    monkeypatch.setattr(C, "time", SimpleNamespace(time=lambda: next(clock)))
    _degrade_search()
    assert C.report(C.SEARCH, active="searxng") is True
    assert C.get(C.SEARCH).since == 200.0


def test_preferred_is_kept_when_not_given():
    _degrade_search(preferred="searxng")
    C.report(C.SEARCH, active="ddgs", state=C.DEGRADED)
    assert C.get(C.SEARCH).preferred == "searxng"


def test_an_unknown_state_is_rejected():
    with pytest.raises(ValueError):
        C.report(C.SEARCH, active="x", state="broken")


def test_all_and_degraded_order_worst_first():
    C.report(C.SANDBOX, active="native")
    C.report(C.MEMORY, active="keyword-only", state=C.DEGRADED)
    C.report(C.MODEL, active="", state=C.UNAVAILABLE)
    assert [e.name for e in C.all()] == [C.MODEL, C.MEMORY, C.SANDBOX]
    assert [e.name for e in C.degraded()] == [C.MODEL, C.MEMORY]


def test_clear_forgets_silently():
    seen = []
    _degrade_search()
    C.subscribe(seen.append)
    assert C.clear(C.SEARCH) is True
    assert C.get(C.SEARCH) is None
    assert C.clear(C.SEARCH) is False
    assert seen == []


def test_names_are_stable_constants():
    assert (C.SEARCH, C.MEMORY, C.MEMORY_EMBED, C.SANDBOX, C.BROWSER, C.MODEL,
            C.CONTEXT, C.MCP, C.OCR, C.DOCUMENTS, C.GATEWAY, C.INSTALL) == (
        "search", "memory", "memory_embed", "sandbox", "browser", "model",
        "context", "mcp", "ocr", "documents", "gateway", "install")
    assert set(C.LABELS) == set(C.NAMES)


# --- subscribers ------------------------------------------------------------------

def test_unsubscribe_stops_notifications():
    seen = []
    unsubscribe = C.subscribe(seen.append)
    unsubscribe()
    unsubscribe()  # twice is harmless
    _degrade_search()
    assert seen == []
    assert not C.has_subscribers()


def test_a_raising_subscriber_never_reaches_the_reporter():
    seen = []

    def broken(_change):
        raise RuntimeError("UI exploded")

    C.subscribe(broken)
    C.subscribe(seen.append)
    assert _degrade_search() is True  # no exception
    assert len(seen) == 1, "later subscribers still run"


def test_recovery_is_flagged():
    seen = []
    _degrade_search()
    C.subscribe(seen.append)
    C.report(C.SEARCH, active="searxng")
    assert seen[0].recovered
    assert seen[0].message == "search upgraded back to searxng"


def test_concurrent_reports_notify_once_per_real_change():
    seen = []
    lock = threading.Lock()

    def record(change):
        with lock:
            seen.append(change)

    C.subscribe(record)
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(50):
            _degrade_search()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 1


# --- renderings -------------------------------------------------------------------

def test_model_note_is_empty_when_ok_or_unknown():
    assert C.model_note(C.SEARCH) == ""
    C.report(C.SEARCH, active="searxng")
    assert C.model_note(C.SEARCH) == ""


def test_model_note_uses_the_reported_note_or_a_default():
    _degrade_search(model_note="[note: custom]")
    assert C.model_note(C.SEARCH) == "[note: custom]"
    C.report(C.MEMORY, active="keyword-only", state=C.DEGRADED, impact="no semantic recall")
    assert C.model_note(C.MEMORY) == \
        "[note: memory is degraded (keyword-only) — no semantic recall]"


def test_banner_line():
    assert C.banner_line() == ""
    C.report(C.SANDBOX, active="native")
    assert C.banner_line() == "", "ok entries are not limited"
    _degrade_search()
    C.report(C.MEMORY, active="keyword-only", state=C.DEGRADED)
    C.report(C.BROWSER, active="", state=C.UNAVAILABLE)
    assert C.banner_line() == \
        "⚠ limited: browser=off · memory=keyword-only · search=ddgs — /doctor"


def test_rows_are_json_safe_and_labelled():
    _degrade_search()
    C.report(C.MEMORY_EMBED, active="keyword", state=C.DEGRADED)
    rows = C.rows()
    json.dumps(rows)
    by_name = {r["name"]: r for r in rows}
    assert by_name["memory_embed"]["label"] == "semantic recall"
    assert set(by_name["search"]) >= {"name", "label", "active", "preferred", "state",
                                      "reason", "impact", "fix", "since"}


def test_change_messages():
    seen = []
    C.subscribe(seen.append)
    _degrade_search()
    C.report(C.BROWSER, active="", state=C.UNAVAILABLE, reason="Chromium missing",
             fix="playwright install chromium")
    assert seen[0].message == ("search using ddgs (SearXNG not answering) — "
                               "keyless scraper; /search setup")
    assert seen[1].message == "browser unavailable (Chromium missing) — playwright install chromium"


def test_doctor_status_only_fails_for_core():
    C.report(C.MODEL, active="", state=C.UNAVAILABLE)
    C.report(C.BROWSER, active="", state=C.UNAVAILABLE)
    _degrade_search()
    assert C.doctor_status(C.get(C.MODEL)) == "fail"
    assert C.doctor_status(C.get(C.BROWSER)) == "warn"
    assert C.doctor_status(C.get(C.SEARCH)) == "warn"


# --- logging route ----------------------------------------------------------------

def test_a_change_is_logged_once_to_the_degraded_logger(caplog):
    caplog.set_level(logging.INFO, logger=C.LOGGER_NAME)
    _degrade_search()
    _degrade_search()
    C.report(C.SEARCH, active="searxng")
    records = [r for r in caplog.records if r.name == C.LOGGER_NAME]
    assert [r.levelno for r in records] == [logging.WARNING, logging.INFO]
    assert all(r.degraded for r in records)
    assert records[0].console is True, "no subscriber: the console handler may echo it"


def test_with_a_subscriber_the_log_record_is_not_for_the_console(caplog):
    caplog.set_level(logging.INFO, logger=C.LOGGER_NAME)
    C.subscribe(lambda change: None)
    _degrade_search()
    record = next(r for r in caplog.records if r.name == C.LOGGER_NAME)
    assert record.console is False


def _record(msg, level=logging.WARNING, **extra):
    record = logging.LogRecord("agent8088.engine", level, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_console_handler_ignores_ordinary_warnings(capsys):
    L.DegradedConsoleHandler().handle(_record("plain warning"))
    assert capsys.readouterr().err == ""


def test_console_handler_echoes_flagged_degradation(capsys):
    L.DegradedConsoleHandler().handle(_record("search switched to ddgs", degraded=True,
                                              console=True))
    assert "agent8088 ⚠ search switched to ddgs" in capsys.readouterr().err


def test_console_handler_steps_aside_for_a_subscriber(capsys):
    handler = L.DegradedConsoleHandler()
    handler.handle(_record("x", degraded=True, console=False))
    # Records flagged by other subsystems (no `console` attr) ask the registry.
    C.subscribe(lambda change: None)
    handler.handle(_record("y", degraded=True))
    assert capsys.readouterr().err == ""


def test_console_handler_does_not_echo_recoveries(capsys):
    L.DegradedConsoleHandler().handle(_record("search upgraded back", logging.INFO,
                                              degraded=True, console=True))
    assert capsys.readouterr().err == ""


@pytest.fixture
def console_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(L.A, "_agent_data_dir", lambda: tmp_path)
    parent = logging.getLogger("agent8088")
    saved = list(parent.handlers)
    parent.handlers = [h for h in saved if not isinstance(
        h, (L.DailyJsonlHandler, L.DegradedConsoleHandler))]
    L.configure_logging()
    yield
    for handler in parent.handlers:
        if handler not in saved:
            handler.close()
    parent.handlers = saved


def test_configure_logging_routes_a_report_to_stderr_and_the_file(console_handler, tmp_path,
                                                                  capsys):
    logging.getLogger("agent8088.engine").warning("ordinary warning")
    _degrade_search()
    err = capsys.readouterr().err
    assert "search using ddgs" in err
    assert "ordinary warning" not in err
    logged = "".join(p.read_text() for p in (tmp_path / "logs").glob("*.log"))
    assert "search using ddgs" in logged and "ordinary warning" in logged


def test_configure_logging_is_quiet_on_stderr_while_a_ui_subscribes(console_handler, capsys):
    C.subscribe(lambda change: None)
    _degrade_search()
    assert capsys.readouterr().err == ""


def test_configure_logging_attaches_one_console_handler(console_handler):
    L.configure_logging()
    parent = logging.getLogger("agent8088")
    assert sum(isinstance(h, L.DegradedConsoleHandler) for h in parent.handlers) == 1


def test_a_backend_swap_reads_as_switched():
    seen = []
    C.report(C.SEARCH, active="searxng")
    C.subscribe(seen.append)
    _degrade_search()
    assert seen[0].message.startswith("search switched to ddgs (SearXNG not answering)")
