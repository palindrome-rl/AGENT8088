"""Problems with where the check runs are not faults of the work.

The boot probe starts a server in a read-only copy on a port of its own, and the page check serves files only. A server
that must create its database file, or whose port is taken, and a page that reports the static server's error page as
an error of its own, have run into the check and not into a defect."""
import pytest

from agent8088 import app_check
from tests.test_app_check import _page, chromium, needs_chromium  # noqa: F401
from tests.test_issue_sheet_39b import _script

SERVER = "import http.server\nPORT = 8000\nhttp.server.HTTPServer(('', PORT), None).serve_forever()\n"


@pytest.mark.parametrize("detail", [
    "sqlite3.OperationalError: unable to open database file",
    "sqlite3.OperationalError: attempt to write a readonly database",
    "OSError: [Errno 30] Read-only file system: 'notes.db'",
    "PermissionError: [Errno 13] Permission denied: 'server.log'",
    "OSError: [Errno 98] Address already in use",
    "Error: listen EADDRINUSE: address already in use :::8000",
    "OSError: [WinError 10048] Only one usage of each socket address",
])
def test_a_server_that_hit_the_probes_surroundings_is_not_verified_rather_than_failed(tmp_path, detail):
    script = _script(tmp_path, SERVER)
    report = app_check.check_script(script, run=lambda c, t: f"BOOT:DIED: {detail}\nCommand exited with status 1.")
    assert report.status == app_check.UNAVAILABLE and not report.failed
    assert "serving was not verified" in report.note


@pytest.mark.parametrize("detail", [
    "RuntimeError: config missing",
    "ModuleNotFoundError: No module named 'flask'",
    "SyntaxError: invalid syntax",
    "NameError: name 'app' is not defined",
])
def test_a_server_that_died_of_its_own_code_still_fails(tmp_path, detail):
    script = _script(tmp_path, SERVER)
    report = app_check.check_script(script, run=lambda c, t: f"BOOT:DIED: {detail}\nCommand exited with status 1.")
    assert report.failed and "exited before accepting" in report.failures[0]


STATUS_TEXT_AS_ERROR = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button><p id="out">waiting</p>
<script>
fetch('/api/items').then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
</script></body></html>"""

HTML_PARSED_AS_JSON = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button><p id="out">waiting</p>
<script>fetch('/api/items').then(r => r.json());</script></body></html>"""

REAL_ERROR_BESIDE_IT = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button><p id="out">waiting</p>
<script>
fetch('/api/items').catch(() => {});
setTimeout(() => { undefinedFunction(); }, 50);
</script></body></html>"""


def _with_server(tmp_path):
    (tmp_path / "server.py").write_text("# the project's own server\n", encoding="utf-8")


@needs_chromium
def test_the_static_servers_error_text_thrown_by_the_page_is_not_a_second_fault(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, STATUS_TEXT_AS_ERROR))
    assert report.status == app_check.PASS, report.failures
    assert any("not counted" in w for w in report.warnings)


@needs_chromium
def test_an_error_page_parsed_as_json_is_not_a_second_fault(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, HTML_PARSED_AS_JSON))
    assert report.status == app_check.PASS, report.failures


@needs_chromium
def test_a_real_page_error_beside_a_missing_backend_still_fails(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, REAL_ERROR_BESIDE_IT))
    assert report.failed and any("undefinedFunction" in f for f in report.failures)


@needs_chromium
def test_without_a_backend_the_same_page_error_still_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, STATUS_TEXT_AS_ERROR))
    assert report.failed


STATUS_CODE_AS_ERROR = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button><p id="out">waiting</p>
<script>
fetch('/api/items').then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
</script></body></html>"""

OTHER_CODE_AS_ERROR = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button><p id="out">waiting</p>
<script>
fetch('/api/items').catch(() => {});
setTimeout(() => { throw new Error('HTTP 500 from the stats module'); }, 50);
</script></body></html>"""


@needs_chromium
def test_an_error_carrying_the_status_code_of_the_missing_backend_is_not_a_second_fault(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, STATUS_CODE_AS_ERROR))
    assert report.status == app_check.PASS, report.failures
    assert any("not counted" in w for w in report.warnings)


@needs_chromium
def test_an_error_with_a_different_code_still_fails(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, OTHER_CODE_AS_ERROR))
    assert report.failed and any("HTTP 500" in f for f in report.failures)
