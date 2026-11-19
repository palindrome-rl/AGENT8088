"""`screenshot_page` renders a local visual file and shows the model the result, with evidence.

It renders any page or image-type file the agent produced (a form, a chart, a canvas app, a
standalone SVG) through check_app's guarded loader: a throwaway server for the file's own folder and
headless Chromium with every request to another host aborted. An image-type file is shown through an
<img> on a wrapper page, so scripts and external references inside it never run.

The evidence beside the image is deterministic: well-formedness for XML-based files, page and console
errors, how much of the image is not background, the difference from the previous render of the same
file, whether the page is still moving, and the image size and saved path.
"""
import json
import re

import pytest
from PIL import Image, ImageDraw

from agent8088 import app_check, render_check


@pytest.fixture
def chromium():
    if not app_check.chromium_available():
        pytest.skip("Chromium for Playwright is not installed")


needs_chromium = pytest.mark.usefixtures("chromium")


@pytest.fixture
def workspace(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    engine._screenshot_counts.clear()
    return tmp_path


def _shoot(engine, path, **extra):
    result = engine.run_tool("screenshot_page", {"path": path, **extra})
    if not result.startswith(engine._IMAGE_MARKER_PREFIX):
        return result, None
    payload = json.loads(result[len(engine._IMAGE_MARKER_PREFIX):])
    return engine._message_text(payload["message"]), payload


HOUSE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="160" viewBox="0 0 200 160">
<rect x="40" y="70" width="120" height="80" fill="#c0392b"/>
<polygon points="30,70 100,15 170,70" fill="#7f8c8d"/>
<rect x="88" y="105" width="24" height="45" fill="#2c3e50"/></svg>"""

FORM_PAGE = """<!doctype html><html><head><style>
body{font-family:sans-serif;margin:24px} label{display:block;margin-top:12px} button{margin-top:16px}
</style></head><body><h1>Sign up</h1><form>
<label>Name <input name="name"></label><label>Email <input name="email" type="email"></label>
<button type="submit">Create account</button></form></body></html>"""

CHART_PAGE = """<!doctype html><html><body><h2>Visits</h2>
<svg width="320" height="160"><rect x="20" y="60" width="40" height="100" fill="#2980b9"/>
<rect x="80" y="20" width="40" height="140" fill="#27ae60"/><rect x="140" y="100" width="40" height="60" fill="#e67e22"/>
<text x="25" y="55">Mon</text><text x="85" y="15">Tue</text><text x="145" y="95">Wed</text></svg></body></html>"""

CANVAS_PAGE = """<!doctype html><html><body><canvas id="c" width="300" height="150"></canvas><script>
const c = document.getElementById('c').getContext('2d'); let x = 0;
function frame(){ c.fillStyle='#fff'; c.fillRect(0,0,300,150); c.fillStyle='#8e44ad';
  c.fillRect(x % 260, 50, 40, 40); x += 6; requestAnimationFrame(frame); }
frame();</script></body></html>"""


# --- registration and refusals (no browser needed) -----------------------------------------------------

def test_screenshot_page_is_a_core_tool(engine):
    spec = engine.TOOL_SPECS.get("screenshot_page")
    assert spec is not None
    assert spec["mode"] == "image"
    assert engine.required_params(spec) == ["path"]
    described = engine.TOOL_SPECS["screenshot_page"]["description"].lower()
    assert "svg" not in described.split(".")[0], "no file type leads the description"


def test_a_broken_svg_is_rejected_with_a_readable_error(engine, workspace):
    (workspace / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>', encoding="utf-8")
    text, payload = _shoot(engine, "logo.svg")
    assert payload is None
    assert "not well formed" in text and re.search(r"line \d+", text)


def test_doctype_and_entity_tricks_are_refused(engine, workspace):
    (workspace / "lighthouse.svg").write_text(
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>', encoding="utf-8")
    text, payload = _shoot(engine, "lighthouse.svg")
    assert payload is None
    assert "DOCTYPE" in text


def test_a_file_that_cannot_be_rendered_is_refused(engine, workspace):
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    text, payload = _shoot(engine, "notes.txt")
    assert payload is None and "cannot render" in text


def test_a_path_outside_the_project_is_refused(engine, workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "page.html"
    outside.write_text(FORM_PAGE, encoding="utf-8")
    text, payload = _shoot(engine, str(outside))
    assert payload is None


def test_without_chromium_the_render_is_unavailable_not_failed(engine, workspace, monkeypatch):
    (workspace / "house.svg").write_text(HOUSE_SVG, encoding="utf-8")
    monkeypatch.setattr(render_check, "chromium_executable", lambda: None)
    text, payload = _shoot(engine, "house.svg")
    assert payload is None
    assert "unavailable" in text.lower() and "was not verified" in text


def test_wait_is_capped(engine):
    assert engine._screenshot_wait({"wait_ms": 999999}) == 3000
    assert engine._screenshot_wait({"wait_ms": "-5"}) == 0


# --- pixel evidence (no browser needed) ---------------------------------------------------------------

def _picture(path, shapes=()):
    img = Image.new("RGB", (200, 120), "white")
    draw = ImageDraw.Draw(img)
    for box, colour in shapes:
        draw.rectangle(box, fill=colour)
    img.save(path, "PNG")
    return path


def test_ink_share_is_zero_for_an_empty_image_and_positive_with_shapes(tmp_path):
    assert render_check.ink_share(_picture(tmp_path / "a.png")) == 0
    assert render_check.ink_share(_picture(tmp_path / "b.png", [((20, 20, 80, 80), "navy")])) > 0.1


def test_diff_share_is_zero_for_identical_images_and_positive_for_a_change(tmp_path):
    first = _picture(tmp_path / "a.png", [((20, 20, 80, 80), "navy")])
    same = _picture(tmp_path / "b.png", [((20, 20, 80, 80), "navy")])
    moved = _picture(tmp_path / "c.png", [((100, 20, 160, 80), "navy")])
    assert render_check.diff_share(first, same) == 0
    assert render_check.diff_share(first, moved) > 0.05


# --- real renders ---------------------------------------------------------------------------------------

@needs_chromium
def test_an_html_form_page_renders_with_evidence(engine, workspace):
    (workspace / "signup.html").write_text(FORM_PAGE, encoding="utf-8")
    text, payload = _shoot(engine, "signup.html")
    assert payload is not None, text
    assert any(part.get("type") == "image_url" for part in payload["message"]["content"])
    assert (workspace / "artifacts" / "screenshots" / "signup_001.png").is_file()
    assert "page errors: none" in text.lower()
    assert re.search(r"\d+ x \d+", text)
    assert "first render of this file" in text.lower()
    assert payload["evidence"]["ink"] > 0


@needs_chromium
def test_a_second_identical_render_says_so_and_a_change_is_measured(engine, workspace):
    page = workspace / "chart.html"
    page.write_text(CHART_PAGE, encoding="utf-8")
    _shoot(engine, "chart.html")
    text, payload = _shoot(engine, "chart.html")
    assert "identical to the previous render" in text.lower()
    assert (workspace / "artifacts" / "screenshots" / "chart_002.png").is_file()
    page.write_text(CHART_PAGE.replace('height="100"', 'height="40"'), encoding="utf-8")
    text, payload = _shoot(engine, "chart.html")
    assert payload["evidence"]["diff"] > 0
    assert "changed from the previous render" in text.lower()


@needs_chromium
def test_a_blank_render_is_flagged(engine, workspace):
    (workspace / "empty.html").write_text("<!doctype html><html><body></body></html>", encoding="utf-8")
    text, payload = _shoot(engine, "empty.html")
    assert "looks blank" in text.lower()


@needs_chromium
def test_page_errors_are_reported(engine, workspace):
    (workspace / "broken.html").write_text(
        "<!doctype html><html><body><p>hi</p><script>undefinedFunction();</script></body></html>", encoding="utf-8")
    text, payload = _shoot(engine, "broken.html")
    assert "undefinedFunction" in text


@needs_chromium
def test_a_request_to_another_host_stays_blocked(engine, workspace):
    (workspace / "remote.html").write_text(
        '<!doctype html><html><body><p>logo</p><img src="https://example.com/logo.png"></body></html>',
        encoding="utf-8")
    text, payload = _shoot(engine, "remote.html")
    assert "blocked" in text.lower() and "example.com" in text


@needs_chromium
def test_a_standalone_svg_renders_and_its_script_stays_inert(engine, workspace):
    (workspace / "house.svg").write_text(HOUSE_SVG.replace(
        "</svg>", '<script>document.documentElement.setAttribute("data-ran","1");'
                  'fetch("https://example.com/beacon")</script>'
                  '<image href="https://example.com/x.png" width="10" height="10"/></svg>'), encoding="utf-8")
    text, payload = _shoot(engine, "house.svg")
    assert payload is not None, text
    assert payload["evidence"]["ink"] > 0
    assert "page errors: none" in text.lower()
    assert "example.com" not in text, "nothing inside the SVG reached the network or ran"


@needs_chromium
def test_a_moving_canvas_is_reported_as_animating(engine, workspace):
    (workspace / "motion.html").write_text(CANVAS_PAGE, encoding="utf-8")
    text, payload = _shoot(engine, "motion.html")
    assert payload["evidence"]["animating"] is True
    assert "still changing" in text.lower()


@needs_chromium
def test_a_phone_width_render(engine, workspace):
    (workspace / "signup.html").write_text(FORM_PAGE, encoding="utf-8")
    text, payload = _shoot(engine, "signup.html", width=375, height=740)
    assert payload["evidence"]["width"] == 375


@needs_chromium
def test_the_model_text_does_not_count_renders(engine, workspace):
    (workspace / "chart.html").write_text(CHART_PAGE, encoding="utf-8")
    for _ in range(3):
        text, _payload = _shoot(engine, "chart.html")
    assert not re.search(r"\b(?:render|attempt|round|try)\s*#?\s*\d", text.lower())


# --- trace ----------------------------------------------------------------------------------------------

@needs_chromium
def test_the_trace_records_the_evidence(engine, workspace, monkeypatch):
    (workspace / "signup.html").write_text(FORM_PAGE, encoding="utf-8")
    replies = ['✿FUNCTION✿: screenshot_page ✿ARGS✿: {"path": "signup.html"}', "It looks fine."]
    calls = []

    def completion(messages, *_a, **_k):
        calls.append(True)
        message = type("Message", (), {"content": replies[min(len(calls) - 1, 1)]})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "_model_accepts_images", lambda: True)
    trace = []
    engine.run_agent([{"role": "user", "content": "Check how signup.html looks."}], max_turns=4,
                     tools_def=[], allowed_tools={"screenshot_page"}, system_prompt="",
                     memory_capture=False, trace=trace)
    evidence = [step for step in trace if step["type"] == "render_evidence"]
    assert evidence and evidence[0]["file"].endswith("signup.html")
    assert any(step["type"] == "image_attached" for step in trace)


# --- tool selection -------------------------------------------------------------------------------------

@pytest.mark.parametrize("request_text", [
    "Build a signup page and check that it looks right on a phone.",
    "Make a bar chart page and make sure the labels are readable.",
    "Check the layout of index.html visually.",
])
def test_a_visual_check_request_is_offered_screenshot_page(engine, monkeypatch, request_text):
    monkeypatch.setattr(engine.memory, "embedder", lambda: None)
    engine._TOOL_INDEX_CACHE.clear()
    chosen = engine.select_tool_names_for_request(request_text, set(engine.TOOL_SPECS), mode="hybrid")
    assert "screenshot_page" in chosen
