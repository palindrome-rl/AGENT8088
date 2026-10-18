"""A page that asks its own server for data is not broken because the page check has no server.

The check serves files only. When the project has a server of its own, a fetch of a route (`/api/status`) that the
static server cannot answer is reported as a warning; a missing file, a missing script and a page error still fail.
"""
import pytest

from agent8088 import app_check
from tests.test_app_check import _page, chromium, needs_chromium  # noqa: F401

ON_LOAD = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button><p id="out">waiting</p>
<script>fetch('/api/status').then(r => r.json()).catch(() => { document.title = 'offline'; });</script>
</body></html>"""

ON_CLICK_POST = """<!doctype html><html><body>
<button id="go" onclick="fetch('/api/ai-move', {method: 'POST', body: '{}'}).catch(() => {});
document.getElementById('out').textContent='asked'">Go</button><p id="out">waiting</p></body></html>"""

MISSING_DATA_FILE = """<!doctype html><html><body>
<button onclick="document.title='x'">Go</button>
<script>fetch('data.json').catch(() => {});</script></body></html>"""

MISSING_SCRIPT = """<!doctype html><html><body>
<button id="go">Go</button><script src="app.js"></script></body></html>"""


def _with_server(tmp_path, name="server.js"):
    (tmp_path / name).write_text("// the project's own server\n", encoding="utf-8")


@needs_chromium
def test_an_api_call_the_static_check_cannot_answer_is_a_warning_when_the_project_has_a_server(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, ON_LOAD))
    assert report.status == app_check.PASS, report.failures
    assert any("/api/status" in w and "backend was not running" in w for w in report.warnings)
    assert not any("Failed to load resource" in f for f in report.failures)


@needs_chromium
def test_a_post_to_a_route_after_a_click_is_a_warning_too(tmp_path):
    _with_server(tmp_path, "app.py")
    report = app_check.check_web_page(_page(tmp_path, ON_CLICK_POST))
    assert report.status == app_check.PASS, report.failures
    assert any("/api/ai-move" in w for w in report.warnings)


@needs_chromium
def test_the_same_page_fails_when_the_project_has_no_server(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, ON_LOAD))
    assert report.status == app_check.FAIL
    assert any("/api/status returned 404" in f for f in report.failures)


@needs_chromium
def test_a_missing_data_file_still_fails_even_with_a_server(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, MISSING_DATA_FILE))
    assert report.status == app_check.FAIL
    assert any("data.json returned 404" in f for f in report.failures)


@needs_chromium
def test_a_missing_script_still_fails_even_with_a_server(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, MISSING_SCRIPT))
    assert report.status == app_check.FAIL
    assert any("app.js" in f for f in report.failures)


@pytest.mark.parametrize("files, expected", [
    ({"server.js": ""}, True),
    ({"app.py": ""}, True),
    ({"package.json": '{"scripts": {"start": "node index.js"}}'}, True),
    ({"package.json": '{"dependencies": {"express": "^4"}}'}, True),
    ({"package.json": '{"dependencies": {"left-pad": "1"}}'}, False),
    ({"requirements.txt": "flask==3.0\n"}, True),
    ({"requirements.txt": "numpy\npandas\n"}, False),
    ({"index.html": "<html></html>", "app.js": "// the page's own script"}, False),
])
def test_has_backend(tmp_path, files, expected):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert app_check.has_backend(tmp_path) is expected


def test_a_server_file_in_the_folder_above_the_page_is_found(tmp_path):
    (tmp_path / "server.js").write_text("", encoding="utf-8")
    page_folder = tmp_path / "public"
    page_folder.mkdir()
    assert app_check.has_backend(page_folder) is True


def test_what_counts_as_a_route():
    assert app_check._is_route("/api/status") and app_check._is_route("/ai-move?x=1")
    assert app_check._is_route("/api/data.json")
    assert not app_check._is_route("/data.json") and not app_check._is_route("/css/site.css?v=2")


@pytest.mark.parametrize("path", ["/api-client.json", "/apiary/data.json", "/apiculture.js"])
def test_api_prefix_is_a_path_segment_not_a_filename_prefix(path):
    assert not app_check._is_route(path)


@needs_chromium
def test_a_missing_api_prefixed_asset_still_fails_with_a_backend(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, MISSING_DATA_FILE.replace("data.json", "api-client.json")))
    assert report.status == app_check.FAIL, report.text()
    assert any("api-client.json returned 404" in failure for failure in report.failures)


def test_a_package_description_is_not_a_start_script(tmp_path):
    (tmp_path / "package.json").write_text('{"description": "start"}', encoding="utf-8")
    assert not app_check.has_backend(tmp_path)
