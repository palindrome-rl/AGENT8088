"""Scripted browser checks against a running service: click, type, wait, and check what the page shows.

check_app loads a page from files and clicks around on its own; it cannot log in, fill a form, or check that a click
showed the right result, and it cannot reach a server. browser_test runs the steps the model gives, in order, in
headless Chromium on this computer, against a service the session started (service_start), and reports each step:

    {"goto": "/login"}                          open a path of the service (same origin only)
    {"click": "button#save"}                    click an element (Playwright selector: CSS, text=..., role=...)
    {"fill": "#email", "value": "a@b.c"}        type into a field, replacing what was there
    {"press": "Enter"} / {"press": "Enter", "selector": "#q"}
    {"select": "#size", "value": "L"}           choose an option
    {"check": "#agree"} / {"uncheck": "#agree"}
    {"wait_for": ".result"}                     wait until an element is visible
    {"expect_text": "Saved"}                    the page shows this text (anywhere, or in "selector")
    {"expect_no_text": "Error"}
    {"expect_visible": ".toast"} / {"expect_hidden": ".spinner"}
    {"expect_count": ".row", "count": 3}
    {"expect_url": "/dashboard"}                the address contains this
    {"reload": true}                            reload the page (state kept by the app, if any)
    {"wait": 500}                               pause, in ms (at most 5000)

For what a selector cannot reach (a canvas game, a drawing app, a drag-and-drop board), the mouse and the keyboard:

    {"click_at": [120, 80]}                     click at page coordinates (CSS pixels, from the top left)
    {"click_at": [40, 30], "selector": "canvas"}  ... relative to an element's top left corner; for a canvas,
                                                in its drawing coordinates (the numbers its own code uses)
        optional "button": "right" / "middle", "clicks": 2
    {"hover": "#menu"} / {"hover": [300, 40]}
    {"drag": [10, 10], "to": [200, 10]}          press, move and release (points, or selectors for both ends;
        "selector" makes points relative to that element)
    {"type": "hello"}                           type into whatever has focus ("selector" clicks it first)
    {"press": "ArrowLeft", "times": 3}          a key several times
    {"hold": "ArrowRight", "ms": 600}           hold a key down (at most 5000 ms), then release it
    {"keydown": "Space"} / {"keyup": "Space"}
    {"viewport": [375, 740]}                    resize the window (a phone-sized screen)

To see the page and plan the next test, and to check what only shows in pixels or in the page's state:

    {"inspect": true}                           list the visible controls, fields, links and canvases with their
                                                positions and a selector for each, plus a screenshot
    {"screenshot": "after the jump"}            capture the window now ("selector" captures one element,
                                                "grid": true draws a labelled coordinate grid for click_at)
    {"expect_changed": "canvas"}                the element (or the window, with true) looks different from the
    {"expect_unchanged": true}                  last screenshot or inspect (or the page as first loaded)
    {"js": "window.game.score"}                 read a value from the page (reported, not a check)
    {"expect_js": "window.game.score > 0"}      a JavaScript expression that must become true (within 2 s)

Screenshots and inspect images are attached to the result for the model to look at (at most MAX_SHOTS in one test).

It stops at the first step that fails and says why. Page errors and console errors are reported. Every request to any
other origin is refused (the page is tested on its own); so are downloads, new windows and permission prompts. A
screenshot of the last state is saved. Nothing here decides what the origin may be: the engine passes the one
service the call names, and refuses any other address before this runs.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_STEPS = 40
STEP_TIMEOUT = 5.0
TOTAL_TIMEOUT = 90.0
MAX_WAIT_MS = 5000
MAX_SHOTS = 4            # screenshots and inspect images one test may attach
MAX_PRESS_TIMES = 50
MAX_INSPECT_ITEMS = 40
VIEWPORT = (1280, 800)
CHANGED_PIXELS = 9       # pixels that must differ for "changed": a 3 x 3 dot, not one stray pixel
ACTIONS = ("goto", "click", "fill", "press", "select", "check", "uncheck", "wait_for", "expect_text",
           "expect_no_text", "expect_visible", "expect_hidden", "expect_count", "expect_url", "reload", "wait",
           "click_at", "hover", "drag", "type", "hold", "keydown", "keyup", "viewport", "inspect", "screenshot",
           "expect_changed", "expect_unchanged", "js", "expect_js")
_CHECKS = ("expect_text", "expect_no_text", "expect_visible", "expect_hidden", "expect_count", "expect_url",
           "expect_changed", "expect_unchanged", "expect_js")
_BUTTONS = ("left", "right", "middle")


@dataclass
class StepResult:
    index: int
    action: str
    ok: bool
    detail: str = ""
    listing: str = ""        # inspect: the controls found, shown under the step


@dataclass
class Shot:
    label: str
    path: str
    width: int
    height: int
    grid: bool = False


@dataclass
class Report:
    url: str
    steps: list = field(default_factory=list)
    page_errors: list = field(default_factory=list)
    console_errors: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    screenshot: str = ""
    error: str = ""          # the test could not run (no browser, bad steps): not a result about the app
    checks: int = 0          # expectation steps that ran
    shots: list = field(default_factory=list)   # Shot: screenshot / inspect images, in order
    shots_dropped: int = 0   # captures past MAX_SHOTS (saved, not attached)

    @property
    def passed(self) -> bool:
        return not self.error and bool(self.steps) and all(s.ok for s in self.steps) and not self.page_errors

    def text(self) -> str:
        if self.error:
            return f"browser test could not run: {self.error}"
        failed = next((s for s in self.steps if not s.ok), None)
        if self.passed:
            head = (f"browser test PASSED: {len(self.steps)} step(s) on {self.url}, "
                    f"{self.checks} expectation(s) met.")
            if not self.checks:
                head += " (No expect_* step: this shows the steps ran, not that the page showed the right thing.)"
        elif failed is not None:
            head = f"browser test FAILED at step {failed.index + 1} ({failed.action}): {failed.detail}"
        else:
            head = "browser test FAILED: the page threw an error: " + self.page_errors[0]
        lines = [head]
        for step in self.steps:
            lines.append(f"  {step.index + 1}. {step.action}: {'ok' if step.ok else 'FAILED'}"
                         + (f" -- {step.detail}" if step.detail else ""))
            if step.listing:
                lines.extend("     " + line for line in step.listing.splitlines())
        if self.page_errors:
            lines.append("page errors: " + " | ".join(self.page_errors[:5]))
        if self.console_errors:
            lines.append("console errors: " + " | ".join(self.console_errors[:5]))
        if self.blocked:
            lines.append("refused (other origins are not reached): " + ", ".join(self.blocked[:5]))
        for n, shot in enumerate(self.shots, 1):
            lines.append(f"image {n} ({shot.label}): {shot.width} x {shot.height} page pixels"
                         + (", with a coordinate grid every 100 px" if shot.grid else "") + f"; {shot.path}")
        if self.shots_dropped:
            lines.append(f"{self.shots_dropped} more capture(s) were saved but not attached (at most {MAX_SHOTS})")
        if self.screenshot:
            lines.append(f"screenshot of the last state: {self.screenshot}")
        return "\n".join(lines)


def parse_steps(steps) -> list[dict]:
    """The steps as a list of one-action dicts. Raises ValueError with a sentence for the model."""
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError as exc:
            raise ValueError(f"steps must be a JSON list of objects: {exc}") from None
    if not isinstance(steps, list) or not steps:
        raise ValueError('steps must be a non-empty JSON list, for example [{"click": "#start"}, {"expect_text": "Done"}]')
    if len(steps) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps in one test; split it")
    parsed = []
    for n, step in enumerate(steps, 1):
        step = _normalized(step)
        if not isinstance(step, dict):
            raise ValueError(f"step {n} is not an object. {STEP_FORM}")
        actions = [a for a in ACTIONS if a in step]
        if len(actions) != 1:
            raise ValueError(f"step {n} needs exactly one action of: {', '.join(ACTIONS)}. {STEP_FORM}")
        action = actions[0]
        _check_step(n, action, step)
        parsed.append(dict(step, _action=action))
    return parsed


STEP_FORM = ('A step is an object keyed by its action, for example {"goto": "/"}, {"click": "#start"}, '
             '{"press": "ArrowLeft"}, {"click_at": [120, 80], "selector": "canvas"}, {"inspect": true}, '
             '{"expect_js": "game.score === 1"}.')
_TRUE_ACTIONS = ("inspect", "screenshot", "reload", "expect_changed", "expect_unchanged")
_POINT_ACTIONS = ("click_at", "hover", "drag")
_SELECTOR_ACTIONS = ("click", "fill", "select", "check", "uncheck", "wait_for", "expect_visible", "expect_hidden",
                     "expect_count", "hover", "drag", "inspect", "expect_changed", "expect_unchanged")
_VALUE_KEYS = {
    "goto": ("url", "path", "value"), "press": ("key", "value"), "keydown": ("key", "value"),
    "keyup": ("key", "value"), "hold": ("key", "value"), "type": ("text", "value"),
    "js": ("expression", "script", "code", "value"), "expect_js": ("expression", "script", "code", "value"),
    "expect_text": ("text", "value"), "expect_no_text": ("text", "value"), "expect_url": ("url", "value"),
    "wait": ("ms", "value"), "screenshot": ("label", "name"), "viewport": ("size", "value"),
}


def _normalized(step):
    """Two shapes models often write, read as the step they mean: a bare action name ("inspect", "reload") and an
    object naming its action ({"action": "click", "selector": "#a"}, {"action": "click_at", "x": 5, "y": 9}). Anything
    else is returned as it is, for parse_steps to explain."""
    if isinstance(step, str) and step in _TRUE_ACTIONS:
        return {step: True}
    if not isinstance(step, dict) or not isinstance(step.get("action"), str) or step["action"] in step:
        return step
    action = step["action"]
    if action not in ACTIONS or any(a in step for a in ACTIONS):
        return step
    rest = {k: v for k, v in step.items() if k != "action"}
    if action in _POINT_ACTIONS and isinstance(rest.get("x"), (int, float)) and isinstance(rest.get("y"), (int, float)):
        value = [rest.pop("x"), rest.pop("y")]
        if action == "drag" and isinstance(rest.get("to_x"), (int, float)) and isinstance(rest.get("to_y"), (int, float)):
            rest["to"] = [rest.pop("to_x"), rest.pop("to_y")]
        return {action: value, **rest}
    for key in ("point", "position", "at") if action in _POINT_ACTIONS else ():
        if key in rest:
            return {action: rest.pop(key), **rest}
    for key in _VALUE_KEYS.get(action, ()):
        if key in rest:
            return {action: rest.pop(key), **rest}
    if action in _SELECTOR_ACTIONS and isinstance(rest.get("selector"), str):
        return {action: rest.pop("selector"), **rest}
    if action in _TRUE_ACTIONS:
        return {action: True, **rest}
    return step


def _is_point(value) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_step(n: int, action: str, step: dict) -> None:
    """The arguments of one step, or a ValueError naming what is wrong."""
    value = step[action]
    where = f"step {n} ({action})"
    if action == "goto" and not (isinstance(value, str) and value):
        raise ValueError(f'{where} needs a path or address on the service, for example {{"goto": "/settings"}}; '
                         "the test already opens the url you gave")
    if action in ("fill", "select") and "value" not in step:
        raise ValueError(f"{where} needs a value")
    if action == "expect_count" and not _is_int(step.get("count")):
        raise ValueError(f"{where} needs an integer count")
    if "selector" in step and not isinstance(step["selector"], str):
        raise ValueError(f"{where}: selector must be a string")
    if action == "click_at":
        if not _is_point(value):
            raise ValueError(f'{where} needs a point [x, y], for example {{"click_at": [120, 80]}}')
        if step.get("button", "left") not in _BUTTONS:
            raise ValueError(f"{where}: button is one of {', '.join(_BUTTONS)}")
        clicks = step.get("clicks", 1)
        if not (_is_int(clicks) and 1 <= clicks <= 3):
            raise ValueError(f"{where}: clicks is 1, 2 or 3")
    elif action == "hover" and not (_is_point(value) or (isinstance(value, str) and value)):
        raise ValueError(f"{where} needs a selector or a point [x, y]")
    elif action == "drag":
        to = step.get("to")
        both_points = _is_point(value) and _is_point(to)
        both_selectors = isinstance(value, str) and bool(value) and isinstance(to, str) and bool(to)
        if not (both_points or both_selectors):
            raise ValueError(f'{where} needs two points ({{"drag": [10, 10], "to": [200, 10]}}) or two selectors '
                             '({"drag": "#card", "to": "#done"})')
    elif action in ("type", "keydown", "keyup", "hold", "js", "expect_js"):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{where} needs a non-empty string")
        ms = step.get("ms", 300)
        if action == "hold" and not (_is_int(ms) and 0 < ms <= MAX_WAIT_MS):
            raise ValueError(f"{where}: ms is a whole number of milliseconds, at most {MAX_WAIT_MS}")
    elif action == "press" and "times" in step:
        if not (_is_int(step["times"]) and 1 <= step["times"] <= MAX_PRESS_TIMES):
            raise ValueError(f"{where}: times is a whole number from 1 to {MAX_PRESS_TIMES}")
    elif action == "viewport":
        if not (_is_point(value) and all(_is_int(v) and 200 <= v <= 4000 for v in value)):
            raise ValueError(f"{where} needs [width, height] in whole pixels, 200 to 4000, for example [375, 740]")
    elif action in ("screenshot", "inspect", "expect_changed", "expect_unchanged"):
        if not (value is True or (isinstance(value, str) and value)):
            raise ValueError(f"{where} takes true, or a string (a label for screenshot, a selector for the others)")


def same_origin(url: str, origin: str) -> bool:
    return url == origin or url.startswith(origin + "/") or url.startswith(origin + "?") or url.startswith(origin + "#")


def run(url: str, steps: list[dict], *, executable: str | None = None, screenshot_dir: Path | None = None,
        step_timeout: float = STEP_TIMEOUT, total_timeout: float = TOTAL_TIMEOUT) -> Report:
    """Run parsed `steps` against `url` (http://127.0.0.1:<port>/...) and report. Runs on its own thread:
    Playwright's sync API starts an event loop, which must not be the caller's."""
    report = Report(url=url)
    out: dict = {}
    worker = threading.Thread(target=_worker, args=(url, steps, executable, screenshot_dir, step_timeout,
                                                    total_timeout, report, out), daemon=True)
    worker.start()
    worker.join(total_timeout + 30)
    if worker.is_alive():
        report.error = report.error or f"the test did not finish within {int(total_timeout)} s"
        browser = out.get("browser")
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
    return report


def _worker(url, steps, executable, screenshot_dir, step_timeout, total_timeout, report: Report, out: dict) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.error = "Playwright is not installed here"
        return
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    started = time.monotonic()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable, args=[f"--agent8088-browser-test={uuid.uuid4().hex}"])
            out["browser"] = browser
            context = browser.new_context(accept_downloads=False, permissions=[], service_workers="block",
                                          viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
            block_websockets = getattr(context, "route_web_socket", None)
            if callable(block_websockets):
                ws_origin = origin.replace("http://", "ws://", 1)

                def on_websocket(ws):
                    if same_origin(ws.url, ws_origin):
                        ws.connect_to_server()
                    else:
                        report.blocked.append(ws.url[:120])
                        ws.close()
                block_websockets("**/*", on_websocket)

            def route(request_route):
                target = request_route.request.url
                if same_origin(target, origin) or target.startswith(("data:", "blob:", "about:")):
                    request_route.continue_()
                else:
                    if target[:120] not in report.blocked:
                        report.blocked.append(target[:120])
                    request_route.abort()
            context.route("**/*", route)
            tab = context.new_page()
            tab.set_default_timeout(step_timeout * 1000)
            tab._agent8088_step_timeout = step_timeout
            tab.on("dialog", lambda dialog: dialog.accept())
            tab.on("pageerror", lambda error: report.page_errors.append(str(error)[:200]))
            def on_console(message):
                # A resource this test refused fails to load: that is the test's rule, not the app's error.
                if message.type == "error" and not (report.blocked and "net::ERR_FAILED" in message.text):
                    report.console_errors.append(message.text[:200])
            tab.on("console", on_console)
            tab.on("popup", lambda popup: popup.close())
            tab.goto(url, wait_until="load")
            run_state = {"report": report, "dir": screenshot_dir, "baseline": None}
            if any(step["_action"] in ("expect_changed", "expect_unchanged") for step in steps):
                run_state["baseline"] = tab.screenshot()   # "changed" before any screenshot step: since the load
            for index, step in enumerate(steps):
                if time.monotonic() - started > total_timeout:
                    report.steps.append(StepResult(index, step["_action"], False,
                                                   f"the test ran out of its {int(total_timeout)} s"))
                    break
                result = _step(tab, step, index, origin, run_state)
                report.steps.append(result)
                if step["_action"] in _CHECKS:
                    report.checks += 1
                if not result.ok:
                    break
            if screenshot_dir is not None:
                try:
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    shot = screenshot_dir / f"browser-test-{uuid.uuid4().hex[:8]}.png"
                    tab.screenshot(path=str(shot))
                    report.screenshot = str(shot)
                except Exception:  # noqa: BLE001 -- the screenshot is a bonus
                    pass
            browser.close()
    except Exception as exc:  # noqa: BLE001 -- a launch or navigation problem is reported, not raised
        message = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
        if not report.steps:
            report.error = f"the page could not be opened: {message}"
        else:
            report.steps.append(StepResult(len(report.steps), "browser", False, message))


def _step(tab, step: dict, index: int, origin: str, run_state: dict | None = None) -> StepResult:
    action = step["_action"]
    selector = step.get(action) if isinstance(step.get(action), str) else step.get("selector")
    if action in _PLAY_ACTIONS:
        # The mouse, the keyboard, captures and page state: their "selector" is the element a point is
        # relative to, or the element captured, never the action's value.
        selector = step.get("selector") if action != "hover" or not isinstance(step["hover"], str) else step["hover"]
        if action in ("inspect", "expect_changed", "expect_unchanged") and isinstance(step[action], str):
            selector = step[action]
    try:
        if action in _PLAY_ACTIONS:
            return _play_step(tab, step, index, action, selector, run_state if run_state is not None else {})
        if action == "goto":
            target = urllib.parse.urljoin(origin + "/", str(step["goto"]))
            if not same_origin(target, origin):
                return StepResult(index, action, False, f"{target} is not on {origin}; a test stays on its service")
            tab.goto(target, wait_until="load")
            return StepResult(index, action, True, target[len(origin):] or "/")
        if action == "click":
            tab.click(selector)
        elif action == "fill":
            tab.fill(selector, str(step["value"]))
        elif action == "press":
            for _ in range(int(step.get("times", 1))):
                if step.get("selector"):
                    tab.press(step["selector"], str(step["press"]))
                else:
                    tab.keyboard.press(str(step["press"]))
            if int(step.get("times", 1)) > 1:
                return StepResult(index, action, True, f"{step['press']} x {int(step['times'])}")
        elif action == "select":
            tab.select_option(selector, str(step["value"]))
        elif action == "check":
            tab.check(selector)
        elif action == "uncheck":
            tab.uncheck(selector)
        elif action == "wait_for":
            tab.wait_for_selector(selector, state="visible")
        elif action == "reload":
            tab.reload(wait_until="load")
        elif action == "wait":
            tab.wait_for_timeout(min(int(step["wait"]), MAX_WAIT_MS))
        elif action in ("expect_text", "expect_no_text"):
            text = str(step[action])
            where = step.get("selector")
            body = (tab.inner_text(where) if where else tab.inner_text("body"))
            if action == "expect_text" and text not in body:
                deadline = time.monotonic() + 2.0   # a result that appears a moment after a click
                while time.monotonic() < deadline and text not in body:
                    tab.wait_for_timeout(200)
                    body = tab.inner_text(where) if where else tab.inner_text("body")
            found = text in body
            if found != (action == "expect_text"):
                shown = " ".join(body.split())[:160]
                return StepResult(index, action, False,
                                  f"{text!r} {'not found' if action == 'expect_text' else 'is shown'}"
                                  f"{' in ' + where if where else ''}; the page shows: {shown!r}")
            return StepResult(index, action, True, repr(text))
        elif action in ("expect_visible", "expect_hidden"):
            state = "visible" if action == "expect_visible" else "hidden"
            tab.wait_for_selector(selector, state=state)
        elif action == "expect_count":
            want = int(step["count"])
            got = tab.locator(selector).count()
            if got != want:
                return StepResult(index, action, False, f"{selector}: {got} found, expected {want}")
            return StepResult(index, action, True, f"{got}")
        elif action == "expect_url":
            if str(step["expect_url"]) not in tab.url:
                return StepResult(index, action, False, f"the address is {tab.url}")
        return StepResult(index, action, True, str(selector or ""))
    except Exception as exc:  # noqa: BLE001 -- a Playwright timeout or a bad selector is the step failing
        first = str(exc).splitlines()[0][:220] if str(exc) else type(exc).__name__
        if "Timeout" in first and selector:
            state = {"expect_hidden": "was still visible", "wait_for": "did not become visible",
                     "expect_visible": "did not become visible"}.get(action, "matched no element that could be used")
            first = f"{selector} {state} within {int(tab_timeout(tab))} s"
        return StepResult(index, action, False, first)


def tab_timeout(tab) -> float:
    return float(getattr(tab, "_agent8088_step_timeout", STEP_TIMEOUT))


# --- the mouse, the keyboard, captures and page state --------------------------------------------------------------

_PLAY_ACTIONS = ("click_at", "hover", "drag", "type", "hold", "keydown", "keyup", "viewport", "inspect", "screenshot",
                 "expect_changed", "expect_unchanged", "js", "expect_js")


class _StepFailed(Exception):
    """A play step that cannot do what it was asked, with the sentence for the model."""


def _box(tab, selector: str) -> dict:
    """The element's box in page coordinates (the first match that is visible)."""
    locator = tab.locator(selector).first
    try:
        locator.wait_for(state="visible", timeout=tab_timeout(tab) * 1000)
    except Exception:  # noqa: BLE001
        raise _StepFailed(f"{selector} matched no visible element within {int(tab_timeout(tab))} s") from None
    box = locator.bounding_box()
    if not box:
        raise _StepFailed(f"{selector} has no box on the page")
    return box


_FRAME_JS = """el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el), canvas = el.tagName === 'CANVAS';
  const pl = parseFloat(s.paddingLeft) || 0, pt = parseFloat(s.paddingTop) || 0;
  const cw = el.clientWidth - pl - (parseFloat(s.paddingRight) || 0);
  const ch = el.clientHeight - pt - (parseFloat(s.paddingBottom) || 0);
  if (!canvas || !el.width || !el.height || cw <= 0 || ch <= 0)
    return {canvas: false, x: r.x, y: r.y, width: r.width, height: r.height};
  return {canvas: true, x: r.x + el.clientLeft + pl, y: r.y + el.clientTop + pt, width: el.width, height: el.height,
          sx: cw / el.width, sy: ch / el.height, cw, ch};
}"""


def _frame(tab, selector: str) -> dict:
    """Where points relative to `selector` start, and their scale. For a canvas: its drawing coordinates (the numbers
    its own code uses), inside the border and padding and through any CSS scaling. For anything else: the element's
    box, from its top left corner."""
    _box(tab, selector)   # visible, or a clear failure
    frame = tab.locator(selector).first.evaluate(_FRAME_JS)
    frame.setdefault("sx", 1.0)
    frame.setdefault("sy", 1.0)
    return frame


def _point(tab, value, selector: str | None) -> tuple[float, float]:
    """A point [x, y] in page coordinates; relative to the element when `selector` is given (_frame)."""
    x, y = float(value[0]), float(value[1])
    if selector:
        frame = _frame(tab, selector)
        if not (0 <= x <= frame["width"] and 0 <= y <= frame["height"]):
            what = f"{selector}'s drawing" if frame["canvas"] else selector
            raise _StepFailed(f"[{value[0]}, {value[1]}] is outside {what}, which is "
                              f"{int(frame['width'])} x {int(frame['height'])}")
        x, y = frame["x"] + x * frame["sx"], frame["y"] + y * frame["sy"]
    size = tab.viewport_size or {"width": VIEWPORT[0], "height": VIEWPORT[1]}
    if not (0 <= x < size["width"] and 0 <= y < size["height"]):
        raise _StepFailed(f"({int(x)}, {int(y)}) is outside the window, which is {size['width']} x {size['height']}")
    return x, y


def _where(x: float, y: float, value, selector: str | None) -> str:
    shown = f"({int(x)}, {int(y)})"
    return f"[{value[0]}, {value[1]}] in {selector} = {shown}" if selector else shown


def _shown(value) -> str:
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= 200 else text[:200] + "..."


def _evaluate(tab, expression: str):
    try:
        return tab.evaluate(expression)
    except Exception as exc:  # noqa: BLE001 -- the page's JavaScript error is the step's result
        first = str(exc).splitlines()[0][:220] if str(exc) else type(exc).__name__
        raise _StepFailed(f"the expression threw: {first}") from None


def _play_step(tab, step: dict, index: int, action: str, selector: str | None, run_state: dict) -> StepResult:
    value = step[action]
    try:
        if action == "click_at":
            x, y = _point(tab, value, selector)
            clicks = int(step.get("clicks", 1))
            tab.mouse.click(x, y, button=step.get("button", "left"), click_count=clicks)
            extra = (f", {step['button']} button" if step.get("button", "left") != "left" else "") + \
                    (f", {clicks} clicks" if clicks > 1 else "")
            return StepResult(index, action, True, _where(x, y, value, selector) + extra)
        if action == "hover":
            if isinstance(value, str):
                tab.hover(value)
                return StepResult(index, action, True, value)
            x, y = _point(tab, value, selector)
            tab.mouse.move(x, y)
            return StepResult(index, action, True, _where(x, y, value, selector))
        if action == "drag":
            to = step["to"]
            if isinstance(value, str):
                tab.drag_and_drop(value, to)
                return StepResult(index, action, True, f"{value} -> {to}")
            x1, y1 = _point(tab, value, selector)
            x2, y2 = _point(tab, to, selector)
            tab.mouse.move(x1, y1)
            tab.mouse.down()
            tab.mouse.move(x2, y2, steps=10)
            tab.mouse.up()
            return StepResult(index, action, True, f"({int(x1)}, {int(y1)}) -> ({int(x2)}, {int(y2)})")
        if action == "type":
            if selector:
                tab.click(selector)
            tab.keyboard.type(value, delay=15)
            return StepResult(index, action, True, repr(value) + (f" into {selector}" if selector else ""))
        if action == "hold":
            ms = int(step.get("ms", 300))
            tab.keyboard.down(value)
            try:
                tab.wait_for_timeout(ms)
            finally:
                tab.keyboard.up(value)
            return StepResult(index, action, True, f"{value} for {ms} ms")
        if action in ("keydown", "keyup"):
            (tab.keyboard.down if action == "keydown" else tab.keyboard.up)(value)
            return StepResult(index, action, True, value)
        if action == "viewport":
            tab.set_viewport_size({"width": int(value[0]), "height": int(value[1])})
            return StepResult(index, action, True, f"{value[0]} x {value[1]}")
        if action == "screenshot":
            label = value if isinstance(value, str) else f"step {index + 1}"
            shot = _capture(tab, run_state, label, selector, bool(step.get("grid")))
            return StepResult(index, action, True, _shot_detail(shot, run_state))
        if action == "inspect":
            listing = _inspect(tab, selector)
            shot = _capture(tab, run_state, "inspect" + (f" {selector}" if selector else ""), selector,
                            bool(step.get("grid")))
            return StepResult(index, action, True, _shot_detail(shot, run_state), listing=listing)
        if action in ("expect_changed", "expect_unchanged"):
            return _expect_changed(tab, step, index, action, selector, run_state)
        if action == "js":
            return StepResult(index, action, True, f"{value} = {_shown(_evaluate(tab, value))}")
        if action == "expect_js":
            result = _evaluate(tab, value)
            deadline = time.monotonic() + 2.0   # a state that settles a moment after a key or a click
            while not result and time.monotonic() < deadline:
                tab.wait_for_timeout(100)
                result = _evaluate(tab, value)
            if not result:
                return StepResult(index, action, False, f"{value} is {_shown(result)}, not true")
            return StepResult(index, action, True, f"{value} ({_shown(result)})")
    except _StepFailed as exc:
        return StepResult(index, action, False, str(exc))
    return StepResult(index, action, False, f"{action} is not handled")


def _capture(tab, run_state: dict, label: str, selector: str | None, grid: bool) -> Shot | None:
    """Capture the window (or one element), save it with an optional coordinate grid, and make it the baseline for
    expect_changed. Returns the Shot, or None when there is nowhere to save it."""
    report: Report = run_state.get("report") or Report(url="")
    if selector:
        frame = _frame(tab, selector)
        if frame["canvas"]:
            # The drawing area only, so that the picture's pixels are the canvas's own coordinates (at CSS scale 1).
            png = tab.screenshot(clip={"x": frame["x"], "y": frame["y"], "width": frame["cw"], "height": frame["ch"]})
            width, height = int(round(frame["cw"])), int(round(frame["ch"]))
        else:
            png = tab.locator(selector).first.screenshot()
            width, height = int(round(frame["width"])), int(round(frame["height"]))
    else:
        png = tab.screenshot()
        size = tab.viewport_size or {"width": VIEWPORT[0], "height": VIEWPORT[1]}
        width, height = size["width"], size["height"]
    run_state["baseline"] = png if not selector else tab.screenshot()
    run_state["captured"] = True
    folder = run_state.get("dir")
    if folder is None:
        return None
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"browser-test-{uuid.uuid4().hex[:8]}.png"
    data = _with_grid(png) if grid else png
    path.write_bytes(data)
    shot = Shot(label=label, path=str(path), width=width, height=height, grid=grid)
    if len(report.shots) >= MAX_SHOTS:
        report.shots_dropped += 1
        run_state["dropped"] = True
    else:
        report.shots.append(shot)
        run_state["dropped"] = False
    return shot


def _shot_detail(shot: Shot | None, run_state: dict) -> str:
    if shot is None:
        return "captured (not saved: no folder for screenshots)"
    if run_state.get("dropped"):
        return f"saved, not attached (more than {MAX_SHOTS} captures): {shot.path}"
    return f"image {len(run_state['report'].shots)} attached"


def _with_grid(png: bytes) -> bytes:
    """The capture with a light grid every 100 px, each line labelled with its coordinate."""
    import io
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return png
    with Image.open(io.BytesIO(png)) as img:
        img = img.convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    width, height = img.size
    try:   # large enough to read after the model's copy is scaled down
        font = ImageFont.load_default(size=18)
    except TypeError:   # Pillow before 10.1: the small bitmap font
        font = ImageFont.load_default()

    def label(x, y, text):
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        draw.rectangle([left - 2, top - 1, right + 2, bottom + 1], fill=(255, 255, 255, 220))
        draw.text((x, y), text, fill=(200, 0, 120, 255), font=font)

    for x in range(100, width, 100):
        draw.line([(x, 0), (x, height)], fill=(255, 0, 160, 120), width=1)
    for y in range(100, height, 100):
        draw.line([(0, y), (width, y)], fill=(255, 0, 160, 120), width=1)
    for x in range(100, width, 100):
        label(x + 3, 2, str(x))
    for y in range(100, height, 100):
        label(3, y + 2, str(y))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _changed_pixels(before: bytes, after: bytes, box: dict | None) -> tuple[int, int]:
    """(pixels that differ, pixels compared) between two window captures, inside `box` (page coordinates) when
    given. A pixel differs when a colour channel moved by more than the render check's tolerance."""
    import io
    from PIL import Image, ImageChops
    from agent8088 import render_check
    with Image.open(io.BytesIO(before)) as a, Image.open(io.BytesIO(after)) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
    if b.size != a.size:
        b = b.resize(a.size, Image.NEAREST)
    if box:
        left, top = max(0, int(box["x"])), max(0, int(box["y"]))
        right, bottom = min(a.size[0], int(box["x"] + box["width"])), min(a.size[1], int(box["y"] + box["height"]))
        if right <= left or bottom <= top:
            return 0, 0
        a, b = a.crop((left, top, right, bottom)), b.crop((left, top, right, bottom))
    # The largest channel difference of each pixel, then a count of those past the tolerance, in Pillow's own code.
    red, green, blue = ImageChops.difference(a, b).split()
    peak = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return sum(peak.histogram()[render_check.CHANNEL_TOLERANCE + 1:]), a.size[0] * a.size[1]


def _expect_changed(tab, step: dict, index: int, action: str, selector: str | None, run_state: dict) -> StepResult:
    before = run_state.get("baseline")
    if before is None:
        before = run_state["baseline"] = tab.screenshot()
    box = _box(tab, selector) if selector else None
    where = selector or "the window"
    differ, total = _changed_pixels(before, tab.screenshot(), box)
    if action == "expect_changed" and differ < CHANGED_PIXELS:
        deadline = time.monotonic() + 2.0   # a frame drawn a moment after the key or the click
        while differ < CHANGED_PIXELS and time.monotonic() < deadline:
            tab.wait_for_timeout(150)
            differ, total = _changed_pixels(before, tab.screenshot(), box)
    changed = differ >= CHANGED_PIXELS
    since = "the last screenshot" if run_state.get("captured") else "the page as first loaded"
    detail = f"{differ} of {total} pixels of {where} ({differ / max(1, total):.1%}) differ from {since}"
    if changed != (action == "expect_changed"):
        return StepResult(index, action, False, detail + ("; nothing visible changed" if not changed
                                                         else "; it was expected to stay the same"))
    return StepResult(index, action, True, detail)


_INSPECT_JS = r"""([scope, limit]) => {
  const root = scope ? document.querySelector(scope) : document.body;
  if (!root) return {missing: true};
  const pick = 'a[href], button, input, select, textarea, canvas, svg, video, [role=button], [role=link], '
    + '[role=checkbox], [role=tab], [role=menuitem], [role=slider], [onclick], [contenteditable=true], [tabindex]';
  const seen = new Set(), items = [];
  const text = el => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title')
    || el.getAttribute('placeholder') || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
  const css = el => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const tag = el.tagName.toLowerCase();
    const name = el.getAttribute('name');
    if (name && document.querySelectorAll(tag + '[name="' + name + '"]').length === 1) return tag + '[name="' + name + '"]';
    const t = text(el);
    if (t && ['button', 'a'].includes(tag) && t.length <= 30 && !t.includes('"')) return tag + ':has-text("' + t + '")';
    if (document.querySelectorAll(tag).length === 1) return tag;
    const path = [];
    let node = el;
    while (node && node !== document.body && path.length < 4) {
      const parent = node.parentElement;
      const same = parent ? [...parent.children].filter(c => c.tagName === node.tagName) : [];
      path.unshift(node.tagName.toLowerCase() + (same.length > 1 ? ':nth-of-type(' + (same.indexOf(node) + 1) + ')' : ''));
      if (node.id) { path[0] = '#' + CSS.escape(node.id); break; }
      node = parent;
    }
    return path.join(' > ');
  };
  for (const el of [root, ...root.querySelectorAll(pick)]) {
    if (!el.matches || !el.matches(pick) || seen.has(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect(), style = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || style.visibility === 'hidden' || style.display === 'none') continue;
    const tag = el.tagName.toLowerCase();
    const kind = tag === 'input' ? 'input[' + (el.type || 'text') + ']' : (el.getAttribute('role') && !['button', 'a'].includes(tag) ? tag + '[role=' + el.getAttribute('role') + ']' : tag);
    const extra = tag === 'canvas' ? ' drawing size ' + el.width + ' x ' + el.height : (el.disabled ? ' disabled' : '');
    items.push({kind, text: text(el), x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                h: Math.round(r.height), selector: css(el), extra});
    if (items.length >= limit) break;
  }
  const total = root.querySelectorAll(pick).length;
  const active = document.activeElement && document.activeElement !== document.body ? css(document.activeElement) : '';
  return {title: document.title, url: location.pathname + location.search, items, total, active,
          page: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
          window: [innerWidth, innerHeight]};
}"""


def _inspect(tab, selector: str | None) -> str:
    """What a user could act on, with where it is and a selector for it: the start of planning a test."""
    data = tab.evaluate(_INSPECT_JS, [selector or "", MAX_INSPECT_ITEMS])
    if data.get("missing"):
        raise _StepFailed(f"{selector} matched nothing to inspect")
    lines = [f"page {data['title']!r} at {data['url']}; window {data['window'][0]} x {data['window'][1]}, "
             f"content {data['page'][0]} x {data['page'][1]}"
             + (f"; focus on {data['active']}" if data.get("active") else "")]
    if not data["items"]:
        lines.append("no visible controls, fields, links or canvases" + (f" in {selector}" if selector else ""))
    for item in data["items"]:
        label = f" {item['text']!r}" if item["text"] else ""
        lines.append(f"{item['kind']}{label} at ({item['x']}, {item['y']}) {item['w']} x {item['h']}"
                     f"{item['extra']} -> {item['selector']}")
    if data["total"] > len(data["items"]):
        lines.append(f"(+{data['total'] - len(data['items'])} more; inspect a part of the page with a selector)")
    return "\n".join(lines)
