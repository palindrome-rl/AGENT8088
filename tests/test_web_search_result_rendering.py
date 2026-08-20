"""on_result() must not dump the raw "[Retrieved ...]" date-stamp and
<<<EXTERNAL_UNTRUSTED_CONTENT>>> tag to the terminal for a web_search
result - that framing is for the model, and reads as broken output to a
user. It should show a clean one-line summary instead, while an error or
an escalation-pending payload (neither of which is a real result set)
still falls through to the plain preview unchanged.
"""
from agent8088 import cli


def _rendered(monkeypatch, result):
    monkeypatch.setattr(cli.S, "verbose", "on")
    printed = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: printed.append(a[0]))
    cli.on_result("web_search", result)
    assert len(printed) == 1
    return printed[0].plain


def test_search_hits_render_as_clean_summary(monkeypatch):
    raw = (
        "[Retrieved 2026-08-20. Check each result's own date before calling "
        "anything current, latest, or upcoming — search results routinely "
        "include older pages.]\n\n"
        '<<<EXTERNAL_UNTRUSTED_CONTENT source="web_search:ddgs">>>\n'
        "Search results (via ddgs):\n\n"
        "1. Rodri (footballer) - Wikipedia\n   https://en.wikipedia.org/wiki/Rodri\n"
        "   Rodri is a Spanish footballer...\n"
        "2. Rodri stats - ESPN\n   https://espn.com/rodri\n   Career stats...\n"
    )
    text = _rendered(monkeypatch, raw)
    assert "Found 2 results via ddgs" in text
    assert "Retrieved" not in text
    assert "EXTERNAL_UNTRUSTED" not in text


def test_no_hits_render_as_clean_summary(monkeypatch):
    raw = (
        "[Retrieved 2026-08-20. Check each result's own date before calling "
        "anything current, latest, or upcoming — search results routinely "
        "include older pages.]\n\nNo results from ddgs."
    )
    text = _rendered(monkeypatch, raw)
    assert text.strip() == "⎿  No results found via ddgs"


def test_error_still_shows_raw_preview(monkeypatch):
    raw = "Error: Blocked — web search queries cannot include a credential."
    text = _rendered(monkeypatch, raw)
    assert "Blocked" in text


def test_escalation_pending_still_shows_raw_preview(monkeypatch):
    raw = "ESCALATION_REQUEST\x1fplan-only\x1fweb_search\x1f\x1fneeds approval"
    text = _rendered(monkeypatch, raw)
    assert "ESCALATION_REQUEST" in text
