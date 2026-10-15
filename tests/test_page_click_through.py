"""The page check keeps clicking after the first control, so a screen that only breaks
on a later click is found, and names the control that broke it."""
import pytest

from agent8088 import app_check

needs_chromium = pytest.mark.usefixtures("chromium")


@pytest.fixture
def chromium():
    if not app_check.chromium_available():
        pytest.skip("Chromium for Playwright is not installed")


def _page(tmp_path, html):
    path = tmp_path / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


TWO_STEPS = """<!doctype html><html><body>
<button id="a" onclick="document.getElementById('b').style.display='inline-block'; this.textContent='A done'">Step one</button>
<button id="b" style="display:none" onclick="boom()">Step two</button>
<script>function boom() { const lines = undefined; lines.forEach(() => {}); }</script>
</body></html>"""


@needs_chromium
def test_an_error_on_the_second_click_fails_the_check_and_names_the_control(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, TWO_STEPS))
    assert report.status == app_check.FAIL, report.text()
    assert any("page error" in f and "after clicking button#b" in f and "Step two" in f
               for f in report.failures), report.failures


@needs_chromium
def test_with_click_through_off_only_the_first_control_is_clicked(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, TWO_STEPS), click_steps=0)
    assert report.status == app_check.PASS, report.text()


@needs_chromium
def test_an_unhandled_rejection_in_an_async_handler_is_found(tmp_path):
    html = """<!doctype html><html><body>
<button id="start" onclick="document.title='started'">Start</button>
<button id="turn" onclick="runTurn()">Next turn</button>
<script>async function runTurn() { await Promise.resolve(); const rows = undefined; rows.forEach(r => r); }</script>
</body></html>"""
    report = app_check.check_web_page(_page(tmp_path, html))
    assert report.status == app_check.FAIL, report.text()
    assert any("after clicking button#turn" in f for f in report.failures), report.failures


@needs_chromium
def test_a_clickable_card_that_is_not_a_button_is_clicked(tmp_path):
    html = """<!doctype html><html><body>
<button onclick="document.title='go'">Start</button>
<div class="card" style="cursor:pointer; width:120px; height:40px; background:#ccc">Pick one</div>
<script>document.querySelector('.card').addEventListener('click', () => { null.missing(); });</script>
</body></html>"""
    report = app_check.check_web_page(_page(tmp_path, html))
    assert report.status == app_check.FAIL, report.text()
    assert any("after clicking div.card" in f for f in report.failures), report.failures


@needs_chromium
def test_a_healthy_multi_screen_page_passes(tmp_path):
    html = """<!doctype html><html><body>
<section id="s1"><button onclick="show('s2')">Next</button></section>
<section id="s2" hidden><button onclick="show('s3')">Continue</button><button onclick="show('s1')">Back</button></section>
<section id="s3" hidden><span id="out">done</span><button onclick="show('s1')">Quit</button></section>
<script>function show(id) { document.querySelectorAll('section').forEach(s => s.hidden = s.id !== id); }</script>
</body></html>"""
    report = app_check.check_web_page(_page(tmp_path, html))
    assert report.status == app_check.PASS, report.text()
    assert not report.failures


@needs_chromium
def test_the_number_of_clicks_is_bounded(tmp_path):
    buttons = "".join(f'<button onclick="document.title=\'b{i}\'">Button {i}</button>' for i in range(30))
    html = f"""<!doctype html><html><body>{buttons}
<button onclick="null.missing()">Last</button></body></html>"""
    assert app_check.check_web_page(_page(tmp_path, html), click_steps=3).status == app_check.PASS
    assert app_check.check_web_page(_page(tmp_path, html), click_steps=40).status == app_check.FAIL


@needs_chromium
def test_a_page_that_navigates_away_stops_the_click_through(tmp_path):
    (tmp_path / "other.html").write_text("<!doctype html><html><body>other</body></html>", encoding="utf-8")
    html = """<!doctype html><html><body>
<button onclick="location.href='other.html'">Leave</button>
<button onclick="null.missing()">Never reached</button></body></html>"""
    report = app_check.check_web_page(_page(tmp_path, html))
    assert report.status == app_check.PASS, report.text()
