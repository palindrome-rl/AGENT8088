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
ACTIONS = ("goto", "click", "fill", "press", "select", "check", "uncheck", "wait_for", "expect_text",
           "expect_no_text", "expect_visible", "expect_hidden", "expect_count", "expect_url", "reload", "wait")
_CHECKS = ("expect_text", "expect_no_text", "expect_visible", "expect_hidden", "expect_count", "expect_url")


@dataclass
class StepResult:
    index: int
    action: str
    ok: bool
    detail: str = ""


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
        if self.page_errors:
            lines.append("page errors: " + " | ".join(self.page_errors[:5]))
        if self.console_errors:
            lines.append("console errors: " + " | ".join(self.console_errors[:5]))
        if self.blocked:
            lines.append("refused (other origins are not reached): " + ", ".join(self.blocked[:5]))
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
        if not isinstance(step, dict):
            raise ValueError(f"step {n} is not an object")
        actions = [a for a in ACTIONS if a in step]
        if len(actions) != 1:
            raise ValueError(f"step {n} needs exactly one action of: {', '.join(ACTIONS)}")
        action = actions[0]
        if action in ("fill", "select") and "value" not in step:
            raise ValueError(f"step {n} ({action}) needs a value")
        if action == "expect_count" and not isinstance(step.get("count"), int):
            raise ValueError(f"step {n} (expect_count) needs an integer count")
        parsed.append(dict(step, _action=action))
    return parsed


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
                                          viewport={"width": 1280, "height": 800})
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
            for index, step in enumerate(steps):
                if time.monotonic() - started > total_timeout:
                    report.steps.append(StepResult(index, step["_action"], False,
                                                   f"the test ran out of its {int(total_timeout)} s"))
                    break
                result = _step(tab, step, index, origin)
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


def _step(tab, step: dict, index: int, origin: str) -> StepResult:
    action = step["_action"]
    selector = step.get(action) if isinstance(step.get(action), str) else step.get("selector")
    try:
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
            if step.get("selector"):
                tab.press(step["selector"], str(step["press"]))
            else:
                tab.keyboard.press(str(step["press"]))
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
