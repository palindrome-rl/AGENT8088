"""A tab that is already the current one must not fail a working page.

Seen in a real run: the page opened on its "Your Team" tab, the check clicked that tab first, nothing changed because it
was already showing, and a working game got the verdict "failed". A control that is already current is judged like any
control that may do nothing by design: it fails the page only if no other control did anything either."""
from agent8088 import app_check
from tests.test_app_check import _page, chromium, needs_chromium  # noqa: F401

ACTIVE_CLASS_TAB = """<!doctype html><html><body>
<nav><button id="tab-player" class="tab active">Your Team</button><button id="tab-ai" class="tab"
  onclick="document.getElementById('view').textContent = 'AI team'">AI Team</button></nav>
<button onclick="document.getElementById('log').textContent += 'rolled '">Randomize</button>
<p id="view">Your team</p><p id="log"></p></body></html>"""

ARIA_SELECTED_TAB = """<!doctype html><html><body>
<div role="tablist"><button role="tab" aria-selected="true">Builder</button>
<button role="tab" aria-selected="false" onclick="document.getElementById('v').textContent='matchup'">Matchup</button></div>
<p id="v">builder</p></body></html>"""

ARIA_PRESSED_TOGGLE = """<!doctype html><html><body>
<button aria-pressed="true">Sound on</button>
<button onclick="document.getElementById('v').textContent='clicked'">Go</button><p id="v">x</p></body></html>"""

CURRENT_BUT_NOTHING_WORKS = """<!doctype html><html><body>
<button class="tab active">One</button><button class="tab">Two</button><p>nothing listens</p></body></html>"""

INACTIVE_DEAD_FIRST = """<!doctype html><html><body>
<button id="go">Go</button><p id="out">waiting</p></body></html>"""

NOT_CURRENT_WORDS = """<!doctype html><html><body>
<button class="inactive-look">Go</button><p id="out">waiting</p></body></html>"""


@needs_chromium
def test_an_already_active_tab_does_not_fail_a_working_page(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, ACTIVE_CLASS_TAB))
    assert report.status == app_check.PASS, report.text()
    assert not report.failures


@needs_chromium
def test_an_aria_selected_tab_does_not_fail_a_working_page(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, ARIA_SELECTED_TAB))
    assert report.status == app_check.PASS, report.text()


@needs_chromium
def test_an_aria_pressed_toggle_does_not_fail_a_working_page(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, ARIA_PRESSED_TOGGLE))
    assert report.status == app_check.PASS, report.text()


@needs_chromium
def test_a_page_where_the_current_tab_is_the_only_thing_that_acts_still_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, CURRENT_BUT_NOTHING_WORKS))
    assert report.status == app_check.FAIL, report.text()
    assert any("changed nothing" in f for f in report.failures), report.failures


@needs_chromium
def test_an_ordinary_dead_button_still_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, INACTIVE_DEAD_FIRST))
    assert report.failed and any("changed nothing" in f for f in report.failures)


@needs_chromium
def test_a_class_that_only_contains_the_word_does_not_count_as_current(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, NOT_CURRENT_WORDS))
    assert report.failed and any("changed nothing" in f for f in report.failures)


def test_the_current_control_rule_is_in_the_script():
    assert "isCurrent" in app_check._COMMITTAL_JS
    assert "aria-selected" in app_check._COMMITTAL_JS and "aria-pressed" in app_check._COMMITTAL_JS
