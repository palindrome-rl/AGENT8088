"""What follows from a call the page check could not answer is not a failure of the page.

Seen in two demo runs: the page asked its own server for /api/roster, the check (which serves files only) answered 404, and the
verdict still read "smoke test FAILED" because (1) the page threw "/api/roster -> 404", (2) the page logged the check's own
"Error response / Error code: 404" page, and (3) no control changed anything since the data never loaded. The backend check of the
same runs had passed: the server started and answered."""
import pytest

from agent8088 import app_check
from tests.test_app_check import _page, chromium, needs_chromium  # noqa: F401

ASKED = {"/api/roster"}
IDLE_CLICK = "clicking the first control (button#go.btn \"Go\") changed nothing on the page, and no other control that was tried changed it either"


def _worker_state():
    return [], [], {}


def test_an_error_that_names_the_route_is_set_aside():
    failures, warnings, out = ["page error: /api/roster -> 404"], [], {}
    app_check._set_aside_missing_backend_effects(failures, warnings, out, ASKED)
    assert failures == [] and "not verified" in warnings[0] and out["unavailable"]


@pytest.mark.parametrize("item", [
    "console error: Error: <!DOCTYPE HTML> <html lang=\"en\"> <head> <meta charset=\"utf-8\"> <title>Error response</title>",
    "page error: Error response Error code: 404 Message: File not found",
])
def test_the_checks_own_error_page_is_set_aside(item):
    failures, warnings, out = [item], [], {}
    app_check._set_aside_missing_backend_effects(failures, warnings, out, ASKED)
    assert failures == [] and out["unavailable"]


def test_a_control_that_did_nothing_is_set_aside_when_the_backend_was_asked():
    failures, warnings, out = [IDLE_CLICK], [], {}
    app_check._set_aside_missing_backend_effects(failures, warnings, out, ASKED)
    assert failures == [] and "follows from the server call" in warnings[0]


def test_other_failures_stay_failures():
    kept = ["page error: Cannot read properties of null (reading 'addEventListener')",
            "console error: Uncaught TypeError: x is not a function",
            "missing or failing file: /js/app.js returned 404"]
    failures, warnings, out = kept + ["page error: /api/roster -> 404"], [], {}
    app_check._set_aside_missing_backend_effects(failures, warnings, out, ASKED)
    assert failures == kept


def test_nothing_is_changed_when_nothing_matches():
    failures, warnings, out = ["page error: boom"], [], {}
    app_check._set_aside_missing_backend_effects(failures, warnings, out, ASKED)
    assert failures == ["page error: boom"] and warnings == [] and "unavailable" not in out


def test_a_blocked_external_call_sets_aside_only_the_idle_control():
    failures, warnings, out = [IDLE_CLICK, "page error: boom"], [], {}
    app_check._set_aside_blocked_call_effects(failures, warnings, out, ["https://pokeapi.co/api/v2/pokemon/garchomp"])
    assert failures == ["page error: boom"] and "pokeapi.co" in warnings[0] and out["unavailable"]


def _server(tmp_path):
    (tmp_path / "server.py").write_text("# the project's own server\n", encoding="utf-8")


NAMED_ROUTE = """<!doctype html><html><body><button id="go">Go</button><p id="out">waiting</p>
<script>fetch('/api/roster').then(r => { if (!r.ok) throw new Error('/api/roster -> ' + r.status); return r.json(); });</script>
</body></html>"""

ERROR_PAGE_THROWN = """<!doctype html><html><body><button id="go">Go</button><p id="out">waiting</p>
<script>fetch('/api/roster').then(r => r.text().then(t => { throw new Error(t); }));</script></body></html>"""

REAL_ERROR_TOO = """<!doctype html><html><body><button id="go">Go</button><p id="out">waiting</p>
<script>fetch('/api/roster').catch(() => {});
setTimeout(() => { undefinedFunction(); }, 50);</script></body></html>"""

EXTERNAL_FETCH = """<!doctype html><html><body><button id="go">Go</button><p id="out">waiting</p>
<script>fetch('https://api.example.com/data').catch(() => {});</script></body></html>"""

IDLE_NO_FETCH = """<!doctype html><html><body><button id="go">Go</button><p id="out">waiting</p></body></html>"""


@needs_chromium
@pytest.mark.parametrize("html", [NAMED_ROUTE, ERROR_PAGE_THROWN])
def test_a_page_that_depends_on_its_own_server_is_not_failed_for_what_follows(tmp_path, html):
    _server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, html))
    assert not report.failed, report.failures
    assert report.status == app_check.UNAVAILABLE and any("not verified" in w for w in report.warnings)


@needs_chromium
def test_a_real_error_beside_the_missing_backend_still_fails(tmp_path):
    _server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, REAL_ERROR_TOO))
    assert report.failed and any("undefinedFunction" in f for f in report.failures)


@needs_chromium
def test_an_idle_page_that_asked_no_one_still_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, IDLE_NO_FETCH))
    assert report.failed and any("changed nothing" in f for f in report.failures)


@needs_chromium
def test_an_idle_control_after_a_blocked_external_fetch_is_not_verified(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, EXTERNAL_FETCH))
    assert not report.failed, report.failures
    assert report.status == app_check.UNAVAILABLE
