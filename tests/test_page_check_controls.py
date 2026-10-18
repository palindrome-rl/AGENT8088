"""The page check judges a control by what it is for.

A dropdown is used by choosing another entry; a Save button of a page with a server of its own answers by asking that
server; and a script a page loads is written for that page. Each used to be reported as a page or script that does not
work, in runs whose work was correct (all three seen in real runs)."""
import pytest

from agent8088 import app_check
from tests.test_app_check import _page, chromium, needs_chromium  # noqa: F401

DROPDOWN = """<!doctype html><html><body>
<select id="fmt"><option>Doubles</option><option>Singles</option></select><p id="out">doubles</p>
<script>document.getElementById('fmt').addEventListener('change', e => {
  document.getElementById('out').textContent = e.target.value; });</script></body></html>"""

DEAD_DROPDOWN = """<!doctype html><html><body>
<select id="fmt"><option>Doubles</option><option>Singles</option></select><p id="out">nothing listens</p></body></html>"""

ONE_OPTION = """<!doctype html><html><body><select id="fmt"><option>Only</option></select></body></html>"""

SAVE = """<!doctype html><html><body>
<input id="t" value="note"><button id="save">Save note</button><p id="out">ready</p>
<script>document.getElementById('save').onclick = () => {
  fetch('/api/notes', {method: 'POST', body: '{}'}).then(r => r.json()).catch(() => {}); };</script></body></html>"""

DEAD_BUTTON = """<!doctype html><html><body><button id="go">Go</button><p id="out">waiting</p></body></html>"""


def _with_server(tmp_path):
    (tmp_path / "server.py").write_text("# the project's own server\n", encoding="utf-8")


@needs_chromium
def test_a_dropdown_that_responds_to_a_choice_passes(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, DROPDOWN))
    assert report.status == app_check.PASS, report.failures


@needs_chromium
def test_a_dropdown_nothing_listens_to_still_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, DEAD_DROPDOWN))
    assert report.failed and any("changed nothing" in f for f in report.failures)


@needs_chromium
def test_a_save_button_that_asks_the_projects_server_is_not_dead(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, SAVE))
    assert report.status == app_check.PASS, report.failures
    assert any("/api/notes" in w for w in report.warnings)


@needs_chromium
def test_a_dead_button_still_fails_on_a_page_with_a_server(tmp_path):
    _with_server(tmp_path)
    report = app_check.check_web_page(_page(tmp_path, DEAD_BUTTON))
    assert report.failed and any("changed nothing" in f for f in report.failures)


@needs_chromium
def test_the_same_save_button_without_a_server_is_not_excused(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, SAVE))
    assert report.failed


# -- scripts a page loads ---------------------------------------------------------------------------

PAGE = '<!doctype html><html><body><script src="js/data.js"></script><script src="js/engine.js?v=2"></script></body></html>'


def _site(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "js" / "data.js").write_text("const POKEMON = [1, 2];\n", encoding="utf-8")
    (tmp_path / "js" / "engine.js").write_text("const first = POKEMON[0];\n", encoding="utf-8")
    (tmp_path / "js" / "other.js").write_text("const x = 1;\n", encoding="utf-8")
    return tmp_path


def test_a_script_a_page_loads_is_found(tmp_path):
    root = _site(tmp_path)
    assert app_check.loaded_by_page(root / "js" / "engine.js").name == "index.html"
    assert app_check.loaded_by_page(root / "js" / "data.js").name == "index.html"
    assert app_check.loaded_by_page(root / "js" / "other.js") is None


def test_a_page_script_is_not_started_alone(tmp_path):
    root = _site(tmp_path)
    ran = []
    report = app_check.check_script(root / "js" / "engine.js", run=lambda command, timeout: ran.append(command) or "")
    assert not ran, "it was started under Node"
    assert report.status == app_check.UNAVAILABLE and "index.html" in report.note and not report.failed


def test_a_script_no_page_loads_is_still_started_and_names_its_error(tmp_path):
    root = _site(tmp_path)
    out = "ReferenceError: POKEMON is not defined\n    at other.js:1\nCommand exited with status 1."
    report = app_check.check_script(root / "js" / "other.js", run=lambda command, timeout: out)
    assert report.failed
    assert "ReferenceError: POKEMON is not defined" in report.failures[0]
    assert "Command exited" not in report.failures[0]


# -- package modules -----------------------------------------------------------------------------------

def _package(tmp_path):
    pkg = tmp_path / "booking"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .rooms import Room\n", encoding="utf-8")
    (pkg / "rooms.py").write_text("from .intervals import overlaps\nclass Room: pass\n", encoding="utf-8")
    (pkg / "intervals.py").write_text("def overlaps(a, b):\n    return a[0] < b[1]\n", encoding="utf-8")
    return pkg


def test_a_package_module_with_relative_imports_is_not_started_alone(tmp_path):
    pkg = _package(tmp_path)
    ran = []
    for name in ("rooms.py", "__init__.py"):
        report = app_check.check_script(pkg / name, run=lambda command, timeout: ran.append(command) or "")
        assert report.status == app_check.UNAVAILABLE and not report.failed and "booking" in report.note
    assert not ran


def test_a_package_module_without_relative_imports_is_still_started(tmp_path):
    pkg = _package(tmp_path)
    ran = []
    app_check.check_script(pkg / "intervals.py", run=lambda command, timeout: ran.append(command) or "")
    assert ran


def test_a_plain_script_outside_a_package_is_still_started(tmp_path):
    (tmp_path / "tool.py").write_text("from . import x\n", encoding="utf-8")   # no package folder: syntax-wise fine
    ran = []
    app_check.check_script(tmp_path / "tool.py", run=lambda command, timeout: ran.append(command) or "")
    assert ran
