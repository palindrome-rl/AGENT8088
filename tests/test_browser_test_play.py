"""browser_test for what a selector cannot reach: the mouse, the keyboard, screenshots, the page's state, and inspect.

A canvas game, a drawing app or a drag-and-drop board is tested by clicking at a point, holding a key, dragging, and
then checking the page's own state (expect_js) or its pixels (expect_changed), and by looking at screenshots. inspect
lists what is on the page and where, to plan the steps. The browser tests run real Chromium against a small game in
this process; they are skipped where no Chromium for Playwright is installed.
"""
import http.server
import json
import threading
from pathlib import Path

import pytest

from agent8088 import browser_test

GAME = """<!doctype html><html><head><title>Square game</title></head><body style="margin:0">
<canvas id="game" width="400" height="300" style="position:absolute;left:50px;top:40px;border:1px solid #000"></canvas>
<div id="card" style="position:absolute;left:500px;top:40px;width:80px;height:40px;background:#fc0">card</div>
<div id="done" style="position:absolute;left:500px;top:200px;width:120px;height:80px;background:#9c9">done</div>
<p id="status" style="position:absolute;left:500px;top:300px">card todo</p>
<input id="name" placeholder="Your name" style="position:absolute;left:50px;top:380px">
<button id="menu" style="position:absolute;left:300px;top:380px">Menu</button>
<ul id="items" style="position:absolute;left:300px;top:410px;display:none"><li>Save</li></ul>
<script>
const c = document.getElementById('game'), ctx = c.getContext('2d');
window.game = {x: 100, score: 0, dots: [], lastDrag: null, keys: {}};
function draw() {
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 400, 300);
  ctx.fillStyle = '#c00'; ctx.fillRect(game.x, 200, 30, 30);
  ctx.fillStyle = '#00c'; for (const d of game.dots) ctx.fillRect(d[0] - 3, d[1] - 3, 6, 6);
}
draw();
document.addEventListener('keydown', e => {
  game.keys[e.key] = true;
  if (e.key === 'ArrowLeft') { game.x -= 10; draw(); }
  if (e.key === 'ArrowRight' && !e.repeat) { game.x += 10; draw(); }
});
document.addEventListener('keyup', e => { game.keys[e.key] = false; });
setInterval(() => { if (game.keys['ArrowRight']) { game.x += 2; draw(); } }, 20);
let start = null;
const at = e => { const r = c.getBoundingClientRect();
  return [(e.clientX - r.left - c.clientLeft) * c.width / c.clientWidth, (e.clientY - r.top - c.clientTop) * c.height / c.clientHeight]; };
c.addEventListener('mousedown', e => { start = at(e); });
c.addEventListener('mouseup', e => {
  const end = at(e);
  if (Math.hypot(end[0] - start[0], end[1] - start[1]) > 5) game.lastDrag = [start, end].map(p => p.map(Math.round));
  else { game.dots.push(end.map(Math.round)); game.score++; draw(); }
});
c.addEventListener('contextmenu', e => { e.preventDefault(); game.right = true; });
c.addEventListener('dblclick', () => { game.double = true; });
let dragging = false;
document.getElementById('card').addEventListener('mousedown', () => { dragging = true; });
document.addEventListener('mouseup', e => {
  if (!dragging) return; dragging = false;
  const r = document.getElementById('done').getBoundingClientRect();
  if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom)
    document.getElementById('status').textContent = 'card done';
});
const menu = document.getElementById('menu'), items = document.getElementById('items');
menu.addEventListener('mouseenter', () => { items.style.display = 'block'; });
</script></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = GAME.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture(scope="module")
def chromium():
    from agent8088 import app_check
    path = app_check.chromium_executable()
    if not path:
        pytest.skip("no Chromium for Playwright is installed here")
    return path


def _run(site, chromium, steps, tmp_path=None):
    return browser_test.run(site, browser_test.parse_steps(json.dumps(steps)), executable=chromium,
                            screenshot_dir=tmp_path)


def _failed(report):
    return next(s for s in report.steps if not s.ok)


# --- steps -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("step, message", [
    ({"click_at": "#game"}, "needs a point [x, y]"),
    ({"click_at": [1, 2, 3]}, "needs a point [x, y]"),
    ({"click_at": [1, True]}, "needs a point [x, y]"),
    ({"click_at": [1, 2], "button": "side"}, "button is one of"),
    ({"click_at": [1, 2], "clicks": 9}, "clicks is 1, 2 or 3"),
    ({"click_at": [1, 2], "selector": 5}, "selector must be a string"),
    ({"hover": ""}, "needs a selector or a point"),
    ({"drag": [1, 2]}, "needs two points"),
    ({"drag": "#a", "to": [1, 2]}, "needs two points"),
    ({"type": ""}, "non-empty string"),
    ({"hold": "ArrowUp", "ms": 60000}, "at most 5000"),
    ({"hold": "ArrowUp", "ms": "long"}, "whole number of milliseconds"),
    ({"keydown": 5}, "non-empty string"),
    ({"press": "a", "times": 0}, "from 1 to 50"),
    ({"press": "a", "times": 500}, "from 1 to 50"),
    ({"viewport": [10, 10]}, "200 to 4000"),
    ({"viewport": "phone"}, "[width, height]"),
    ({"screenshot": 3}, "takes true, or a string"),
    ({"inspect": False}, "takes true, or a string"),
    ({"expect_js": ""}, "non-empty string"),
])
def test_bad_play_steps_are_explained(step, message):
    with pytest.raises(ValueError, match=r".*" + message.replace("[", r"\[").replace("]", r"\]")):
        browser_test.parse_steps([step])


def test_good_play_steps_parse():
    steps = [{"click_at": [10, 20], "selector": "canvas", "button": "right", "clicks": 2}, {"hover": [1, 1]},
             {"drag": "#a", "to": "#b"}, {"drag": [1, 1], "to": [5.5, 9]}, {"type": "hi", "selector": "#q"},
             {"hold": "ArrowRight", "ms": 400}, {"keydown": "Space"}, {"keyup": "Space"}, {"press": "a", "times": 3},
             {"viewport": [375, 740]}, {"inspect": True}, {"screenshot": "after", "grid": True},
             {"expect_changed": "canvas"}, {"expect_unchanged": True}, {"js": "1 + 1"}, {"expect_js": "true"}]
    assert [s["_action"] for s in browser_test.parse_steps(steps)] == [
        "click_at", "hover", "drag", "drag", "type", "hold", "keydown", "keyup", "press", "viewport", "inspect",
        "screenshot", "expect_changed", "expect_unchanged", "js", "expect_js"]


# --- in a real browser ---------------------------------------------------------------------------------------------


def test_inspect_lists_what_can_be_used_with_where_it_is_and_a_selector(site, chromium, tmp_path):
    report = _run(site, chromium, [{"inspect": True}], tmp_path)
    listing = report.steps[0].listing
    assert "'Square game'" in listing and "window 1280 x 800" in listing
    assert "canvas at (50, 40) 402 x 302 drawing size 400 x 300 -> #game" in listing
    assert "input[text] 'Your name' at (50, 380)" in listing and "-> #name" in listing
    assert "button 'Menu'" in listing and "-> #menu" in listing
    assert len(report.shots) == 1 and Path(report.shots[0].path).is_file()
    text = report.text()
    assert "-> #game" in text and "image 1 (inspect): 1280 x 800 page pixels" in text
    # Looking proves nothing about the app.
    assert report.passed and "No expect_* step" in text


def test_inspect_of_a_part_of_the_page_and_of_nothing(site, chromium, tmp_path):
    report = _run(site, chromium, [{"inspect": "#done"}], tmp_path)
    assert "no visible controls" in report.steps[0].listing and report.shots[0].width == 120
    report = _run(site, chromium, [{"inspect": "#nope"}], tmp_path)
    assert not report.steps[0].ok and "#nope" in report.steps[0].detail


def test_keys_move_the_player(site, chromium):
    report = _run(site, chromium, [
        {"press": "ArrowRight", "times": 3}, {"expect_js": "game.x === 130"},
        {"press": "ArrowLeft"}, {"expect_js": "game.x === 120"},
        {"hold": "ArrowRight", "ms": 400}, {"expect_js": "game.x > 130"}, {"expect_js": "game.keys.ArrowRight === false"},
        {"keydown": "a"}, {"expect_js": "game.keys.a === true"}, {"keyup": "a"}, {"expect_js": "game.keys.a === false"},
        {"js": "game.x"},
    ])
    assert report.passed, report.text()
    assert report.steps[0].detail == "ArrowRight x 3"
    assert report.steps[-1].detail.startswith("game.x = ")


def test_click_at_a_point_of_the_canvas(site, chromium):
    report = _run(site, chromium, [
        {"click_at": [20, 30], "selector": "#game"}, {"expect_js": "JSON.stringify(game.dots) === '[[20,30]]'"},
        {"click_at": [171, 101]}, {"expect_js": "game.score === 2"},          # page coordinates: (120, 60) in it
        {"click_at": [5, 5], "selector": "#game", "button": "right"}, {"expect_js": "game.right === true"},
        {"click_at": [9, 9], "selector": "#game", "clicks": 2}, {"expect_js": "game.double === true"},
    ])
    assert report.passed, report.text()
    assert report.steps[0].detail == "[20, 30] in #game = (71, 71)"     # inside its 1 px border


@pytest.mark.parametrize("step, message", [
    ({"click_at": [500, 10], "selector": "#game"}, "[500, 10] is outside #game's drawing, which is 400 x 300"),
    ({"click_at": [700, 10], "selector": "#done"}, "[700, 10] is outside #done, which is 120 x 80"),
    ({"click_at": [1300, 10]}, "(1300, 10) is outside the window, which is 1280 x 800"),
    ({"click_at": [1, 1], "selector": "#nope"}, "#nope matched no visible element"),
])
def test_a_point_that_misses_says_so(site, chromium, step, message):
    report = _run(site, chromium, [step])
    assert not report.passed and message in _failed(report).detail


def test_drag_with_points_and_with_selectors(site, chromium):
    report = _run(site, chromium, [
        {"drag": [10, 10], "to": [200, 120], "selector": "#game"},
        {"expect_js": "JSON.stringify(game.lastDrag) === '[[10,10],[200,120]]'"},
        {"expect_text": "card todo"}, {"drag": "#card", "to": "#done"}, {"expect_text": "card done"},
    ])
    assert report.passed, report.text()


def test_type_hover_and_viewport(site, chromium):
    report = _run(site, chromium, [
        {"type": "Ada", "selector": "#name"}, {"expect_js": "document.getElementById('name').value === 'Ada'"},
        {"expect_hidden": "#items"}, {"hover": "#menu"}, {"expect_visible": "#items"},
        {"viewport": [375, 740]}, {"expect_js": "innerWidth === 375"},
    ])
    assert report.passed, report.text()


def test_expect_changed_and_unchanged_read_the_pixels(site, chromium, tmp_path):
    report = _run(site, chromium, [
        {"expect_unchanged": "#game"},                       # nothing happened since the load
        {"press": "ArrowRight"}, {"expect_changed": "#game"},
        {"screenshot": "after one step"}, {"expect_unchanged": True},
        {"click_at": [5, 5], "selector": "#game"}, {"expect_changed": "#game"},
    ], tmp_path)
    assert report.passed, report.text()
    assert "differ from the page as first loaded" in report.steps[0].detail
    assert "differ from the last screenshot" in report.steps[4].detail
    report = _run(site, chromium, [{"expect_changed": "#game"}], tmp_path)
    assert not report.passed and "nothing visible changed" in _failed(report).detail
    report = _run(site, chromium, [{"press": "ArrowLeft"}, {"expect_unchanged": "#game"}], tmp_path)
    assert not report.passed and "expected to stay the same" in _failed(report).detail


def test_a_state_that_is_not_true_fails_with_its_value(site, chromium):
    report = _run(site, chromium, [{"expect_js": "game.score"}])
    assert not report.passed and "game.score is 0, not true" in _failed(report).detail
    report = _run(site, chromium, [{"js": "game.nothing.deeper"}])
    assert not report.passed and "the expression threw" in _failed(report).detail
    report = _run(site, chromium, [{"press": "NotAKey"}])
    assert not report.passed and "NotAKey" in _failed(report).detail


def test_screenshots_are_saved_with_a_grid_when_asked_and_capped(site, chromium, tmp_path):
    from PIL import Image
    report = _run(site, chromium, [{"screenshot": "plain"}, {"screenshot": "grid", "grid": True},
                                   {"screenshot": "canvas", "selector": "#game"}, {"screenshot": "4"},
                                   {"screenshot": "5"}], tmp_path)
    assert report.passed
    assert [s.label for s in report.shots] == ["plain", "grid", "canvas", "4"] and report.shots_dropped == 1
    plain, grid, canvas = (Image.open(s.path).convert("RGB") for s in report.shots[:3])
    assert plain.size == grid.size == (1280, 800) and canvas.size == (400, 300)   # the drawing, not its border
    assert plain.getpixel((100, 600)) != grid.getpixel((100, 600))        # the grid line at x = 100
    assert "with a coordinate grid every 100 px" in report.text()
    assert "1 more capture(s) were saved but not attached" in report.text()
    assert "saved, not attached" in report.steps[4].detail


# --- the engine ---------------------------------------------------------------------------------------------------


@pytest.fixture
def served(engine, site, chromium, tmp_path, monkeypatch):
    from urllib.parse import urlsplit
    from agent8088 import services
    port = urlsplit(site).port
    registry = services.Registry(tmp_path / "services.json", tmp_path / "logs", pid=4242)
    registry.services["game"] = services.Service("game", "x", "main", port, "native", Path("log"),
                                                 services.Handle(stop=lambda: None, alive=lambda: True, host_port=port))
    monkeypatch.setattr(engine, "_services_registry", registry)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "_playwright_chromium_executable", lambda: chromium)
    return engine


def _payload(engine, out):
    assert out.startswith(engine._IMAGE_MARKER_PREFIX), out[:200]
    payload = json.loads(out[len(engine._IMAGE_MARKER_PREFIX):])
    parts = payload["message"]["content"]
    return payload, parts[0]["text"], [p for p in parts if p.get("type") == "image_url"]


def test_screenshots_reach_the_model_as_images(served):
    engine = served
    port = engine._services().services["game"].handle.host_port
    out = engine._exec_browser_test({"url": f"http://127.0.0.1:{port}/",
                                     "steps": json.dumps([{"inspect": True}, {"press": "ArrowRight"},
                                                          {"screenshot": "moved", "grid": True},
                                                          {"expect_js": "game.x === 110"}])})
    payload, text, images = _payload(engine, out)
    assert len(images) == 2 and images[0]["image_url"]["url"].startswith("data:image/")
    assert text.startswith("Tool result (browser_test):") and "<<<EXTERNAL_UNTRUSTED_CONTENT" in text
    assert "image 1: inspect" in text and "image 2: moved" in text and "-> #game" in text
    assert "not the attached image's own size" in text
    assert engine._browser_test_status("browser_test", out, "unknown") == "passed"
    assert Path(engine.ARTIFACTS_ROOT, "screenshots", "browser-test").is_dir()


def test_a_failed_test_attaches_the_state_it_stopped_in(served):
    engine = served
    port = engine._services().services["game"].handle.host_port
    out = engine._exec_browser_test({"url": f"http://127.0.0.1:{port}/",
                                     "steps": json.dumps([{"press": "ArrowRight"}, {"expect_js": "game.x === 999"}])})
    payload, text, images = _payload(engine, out)
    assert len(images) == 1 and "the state when the test stopped" in text
    assert engine._browser_test_status("browser_test", out, "unknown") == "failed"


def test_no_capture_and_a_pass_is_plain_text(served):
    engine = served
    port = engine._services().services["game"].handle.host_port
    out = engine._exec_browser_test({"url": f"http://127.0.0.1:{port}/", "steps": json.dumps([{"expect_js": "true"}])})
    assert not out.startswith(engine._IMAGE_MARKER_PREFIX) and "browser test PASSED" in out


def test_only_as_many_images_as_the_conversation_keeps_the_latest(served, monkeypatch):
    engine = served
    monkeypatch.setattr(engine, "_image_keep", lambda: 2)
    port = engine._services().services["game"].handle.host_port
    out = engine._exec_browser_test({"url": f"http://127.0.0.1:{port}/", "steps": json.dumps(
        [{"screenshot": "one"}, {"screenshot": "two"}, {"screenshot": "three"}])})
    _payload_, text, images = _payload(engine, out)
    assert len(images) == 2 and "image 1: two" in text and "image 2: three" in text


def test_the_agent_loop_hands_the_model_the_images(served, monkeypatch):
    """The result goes through the loop like screenshot_page's: an image turn, and the verdict read from its text."""
    engine = served
    port = engine._services().services["game"].handle.host_port
    steps = json.dumps([{"screenshot": "start"}, {"expect_js": "game.x === 100"}])
    call = json.dumps({"url": f"http://127.0.0.1:{port}/", "steps": steps})
    replies = [f"✿FUNCTION✿: browser_test ✿ARGS✿: {call}", "The square is at the start."]
    seen = []

    def completion(messages, *_a, **_k):
        seen.append(list(messages))
        message = type("Message", (), {"content": replies[min(len(seen) - 1, 1)]})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "_model_accepts_images", lambda: True)
    engine.PERMISSION_MODE = "full-auto"
    trace = []
    engine.run_agent([{"role": "user", "content": "Test the game in the browser."}], max_turns=4,
                     tools_def=[], allowed_tools={"browser_test"}, system_prompt="",
                     memory_capture=False, trace=trace)
    assert any(step["type"] == "image_attached" and step["tool"] == "browser_test" for step in trace), trace
    image_turns = [m for m in seen[-1] if isinstance(m.get("content"), list)
                   and any(part.get("type") == "image_url" for part in m["content"])]
    assert len(image_turns) == 1 and "browser test PASSED" in image_turns[0]["content"][0]["text"]


def test_a_point_on_a_scaled_canvas_is_in_its_drawing_coordinates(site, chromium, tmp_path):
    """A canvas shown at twice its drawing size: [20, 30] is where its own code draws (20, 30), not 20 CSS pixels."""
    report = _run(site, chromium, [
        {"js": "(c.style.width = '800px', c.style.height = '600px', 1)"},
        {"click_at": [20, 30], "selector": "#game"}, {"expect_js": "JSON.stringify(game.dots) === '[[20,30]]'"},
        {"screenshot": "scaled", "selector": "#game"},
    ], tmp_path)
    assert report.passed, report.text()
    assert report.steps[1].detail == "[20, 30] in #game = (91, 101)"
    assert report.shots[0].width == 800 and report.shots[0].height == 600


@pytest.mark.parametrize("step, want", [
    ("inspect", {"inspect": True}),
    ("reload", {"reload": True}),
    ({"action": "inspect"}, {"inspect": True}),
    ({"action": "goto", "url": "/play"}, {"goto": "/play"}),
    ({"action": "click", "selector": "#start"}, {"click": "#start"}),
    ({"action": "fill", "selector": "#name", "value": "Ada"}, {"fill": "#name", "value": "Ada"}),
    ({"action": "press", "key": "ArrowLeft", "times": 2}, {"press": "ArrowLeft", "times": 2}),
    ({"action": "click_at", "x": 120, "y": 80, "selector": "canvas"}, {"click_at": [120, 80], "selector": "canvas"}),
    ({"action": "drag", "x": 1, "y": 2, "to_x": 30, "to_y": 40}, {"drag": [1, 2], "to": [30, 40]}),
    ({"action": "expect_js", "expression": "game.score === 1"}, {"expect_js": "game.score === 1"}),
    ({"action": "expect_text", "text": "Saved", "selector": "#out"}, {"expect_text": "Saved", "selector": "#out"}),
    ({"action": "screenshot", "label": "start"}, {"screenshot": "start"}),
])
def test_the_shapes_models_often_write_are_read_as_meant(step, want):
    parsed = browser_test.parse_steps([step])[0]
    assert {k: v for k, v in parsed.items() if k != "_action"} == want


@pytest.mark.parametrize("steps, message", [
    (["goto"], 'object keyed by its action, for example {"goto": "/"}'),
    ([{"action": "teleport"}], "needs exactly one action"),
    ([{"goto": True}], "needs a path or address on the service"),
    ([{"goto": {}}], "needs a path or address on the service"),
])
def test_a_wrong_shape_is_explained_with_examples(steps, message):
    with pytest.raises(ValueError) as caught:
        browser_test.parse_steps(steps)
    assert message in str(caught.value)
