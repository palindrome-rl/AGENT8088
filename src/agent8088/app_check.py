"""Does the thing the run built actually work? A deterministic smoke test.

A web page is loaded in headless Chromium (Playwright) from a throwaway static
server bound to 127.0.0.1 that serves only the page's own folder. Requests to any
other host are intercepted and aborted, so untrusted page code cannot reach the
network, and nothing here loosens the SSRF rules or the browse_page gating. The
page fails the check on a page error, a console error from its own code, a
missing or failing own-origin asset, a first control that an overlay blocks, or a
first control whose click changes nothing. After that first click it keeps
clicking the next visible controls (a bounded number, a bounded time) and fails on
any page error those clicks raise, so a screen that only breaks on the third click
is found too. A request to an external host (hotlinked fonts, images, CDN scripts)
is only a warning.

A script must compile (Python) or parse (Node). When a read-only sandbox runner
is supplied it also gets a brief startup check there. A script whose source
actually listens for connections is a server: it is started in that sandbox and
must accept a connection on the port its own source declares; a server whose
port cannot be determined is reported as unverified, not guessed. Without
Chromium the page check reports "unavailable" instead of failing the task.
Dependency errors from blocked external scripts are inconclusive, never proof
that the page passed.
"""

from __future__ import annotations

import base64
import functools
import re
import shlex
import time
import http.server
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

WEB_SUFFIXES = frozenset({".html", ".htm"})
PYTHON_SUFFIXES = frozenset({".py"})
NODE_SUFFIXES = frozenset({".js", ".mjs", ".cjs"})
DEFAULT_TIMEOUT = 20.0
# How long a started server gets to open its port, and the sandbox clock for the
# whole probe (spawn overhead plus the wait). A refused port costs the full
# window; serving or crashing returns as soon as it happens.
BOOT_WAIT = 10.0
BOOT_RUN_TIMEOUT = 15


CLICK_THROUGH_STEPS = 12   # further controls clicked after the first; 0 = only the first
MAX_ITEMS = 5
MAX_REPORT_CHARS = 1800

PASS, FAIL, UNAVAILABLE, SKIPPED = "pass", "fail", "unavailable", "skipped"


@dataclass
class Report:
    kind: str                     # "web" or "script"
    target: str
    status: str                   # pass | fail | unavailable | skipped
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    note: str = ""
    output: str = ""              # what a script printed when started (clipped); for diagnosis only
    ask: bool = False             # not verified, and worth asking the model to run it itself

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    def summary(self) -> str:
        name = Path(self.target).name or self.target
        if self.status == PASS:
            tail = f" ({len(self.warnings)} warning{'s' if len(self.warnings) != 1 else ''})" if self.warnings else ""
            # "compiles; not executed" is a pass only in status; say so, or the
            # summary reads as if the script had been run.
            ran = f" - {self.note}" if "not executed" in self.note else ""
            return f"{name}: smoke test passed{tail}{ran}"
        if self.status == FAIL:
            return f"{name}: smoke test FAILED - " + "; ".join(self.failures[:2])
        return f"{name}: smoke test {self.status}" + (f" ({self.note})" if self.note else "")

    def text(self, limit: int = MAX_REPORT_CHARS) -> str:
        """The report the model reads: what failed, what only warned, the console."""
        lines = [self.summary()]
        if self.status == FAIL:
            lines += [f"- {item}" for item in self.failures[:MAX_ITEMS]]
        if self.warnings:
            lines.append("Warnings (not failures):")
            lines += [f"- {item}" for item in self.warnings[:MAX_ITEMS]]
        if self.console and self.status != PASS:
            lines.append("Console:")
            lines += [f"  {item}" for item in self.console[:MAX_ITEMS]]
        if self.note and self.status in (PASS, FAIL):
            lines.append(self.note)
        return "\n".join(lines)[:limit]


# --- Chromium ------------------------------------------------------------------------------

def chromium_available() -> bool:
    """Whether Playwright's Chromium is installed.

    The answer depends on where Playwright looks, which the engine sets through
    PLAYWRIGHT_BROWSERS_PATH, so it is cached per value of that variable rather
    than once per process.
    """
    key = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if key in _CHROMIUM_ANSWERS:
        return _CHROMIUM_ANSWERS[key]
    answer = _chromium_available(key)
    # A probe that timed out says nothing about the install: ask again next time
    # instead of switching smoke tests off for the rest of the session.
    if answer is not None:
        _CHROMIUM_ANSWERS[key] = answer
    return bool(answer)


_CHROMIUM_ANSWERS: dict = {}


def _chromium_available(browsers_path: str):
    """True/False when known, None when the probe did not finish in time.
    Probed on its own thread: the sync API starts an event loop, which must not
    be the caller's."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    found: list[bool] = []

    def probe():
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                found.append(os.path.exists(p.chromium.executable_path))
        except Exception:  # noqa: BLE001 -- any launch problem means "not available"
            found.append(False)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(30)
    return found[0] if found else None


_PARENT_REF = re.compile(r"""(?:src|href)\s*=\s*["']((?:\.\./)+)""", re.IGNORECASE)


def serve_root(page: Path) -> Path:
    """The folder to serve: the page's own, or the ancestor its `../` links reach.

    A page in docs/ that loads ../style.css needs the folder above it, or every
    correct site with a shared stylesheet is reported broken. Bounded to three
    levels and never above the filesystem root.
    """
    page = Path(page)
    try:
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return page.parent
    depth = max((len(m.group(1)) // 3 for m in _PARENT_REF.finditer(text)), default=0)
    root = page.parent
    for _ in range(min(depth, 3)):
        if root.parent == root:
            break
        root = root.parent
    return root


# --- the static server -------------------------------------------------------------------------

def _handler(root: Path, deny: Callable[[str], bool] | None):
    real_root = os.path.realpath(root)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):  # silence
            pass

        def translate_path(self, path):
            translated = super().translate_path(path)
            real = os.path.realpath(translated)
            # Nothing outside the page's own folder, symlinks included, and
            # nothing the harness treats as a credential file.
            if real != real_root and not real.startswith(real_root + os.sep):
                return os.path.join(real_root, "__outside_root__")
            if deny is not None and deny(real):
                return os.path.join(real_root, "__denied__")
            return translated

        def list_directory(self, path):  # no folder listings, only files and index pages
            self.send_error(404)
            return None

        def do_POST(self):  # GET and HEAD only
            # Closing with an unread request body can discard the 405 response
            # as a TCP reset on Windows. Drain only a small, time-bounded body;
            # never execute the request or wait indefinitely for its sender.
            try:
                remaining = min(65536, max(0, int(self.headers.get("Content-Length", "0"))))
            except (ValueError, OverflowError):
                remaining = 0
            previous_timeout = self.connection.gettimeout()
            deadline = time.monotonic() + 0.2
            try:
                while remaining and time.monotonic() < deadline:
                    self.connection.settimeout(min(0.2, max(0.001, deadline - time.monotonic())))
                    chunk = self.rfile.read1(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass
            finally:
                self.connection.settimeout(previous_timeout)
            self.send_error(405)

        do_PUT = do_DELETE = do_PATCH = do_POST

    return Handler


# --- the page check ------------------------------------------------------------------------------

# Whether a click on a control is expected to change the page. A filter, an undo, an export or an
# "Add" button next to an empty field can all do nothing by design, so a click that changes nothing
# says something about the page only when the control is one that must act.
_COMMITTAL_JS = """
  const NOOP_OK = /\\b(undo|redo|close|cancel|back|previous|prev|reset|clear|delete|remove|exit|quit|forfeit|export|import|download|upload|print|copy|share|save|mute|sound|audio|music|help|about|settings|menu|more|toggle|theme)\\b/i;
  const emptyField = x => {
    const t = (x.getAttribute('type') || 'text').toLowerCase();
    if (['button', 'submit', 'checkbox', 'radio', 'hidden', 'file', 'reset', 'image'].includes(t)) return false;
    const r = x.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !x.value;
  };
  const hasEmptyField = root => (root.matches && root.matches('input, textarea') && emptyField(root))
    || [...(root.querySelectorAll ? root.querySelectorAll('input, textarea') : [])].some(emptyField);
  const emptyFieldNear = el => {
    let p = el.parentElement;
    if (p === document.body) {   // a loose toolbar: only the neighbours count, not the whole page
      const kids = [...p.children], at = kids.indexOf(el);
      return kids.slice(Math.max(0, at - 3), at + 4).some(hasEmptyField);
    }
    for (let i = 0; i < 2 && p && p !== document.body; i++, p = p.parentElement) {
      if (hasEmptyField(p)) return true;
    }
    return false;
  };
  const labelOf = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
  const committal = el => el.tagName !== 'SELECT' && el.getAttribute('aria-disabled') !== 'true'
    && !/disabled/i.test(typeof el.className === 'string' ? el.className : '')
    && !NOOP_OK.test(labelOf(el)) && !emptyFieldNear(el);
"""

_FIRST_CONTROL_JS = """() => {
  const selector = 'button, input[type=button], input[type=submit], [role=button], [onclick], summary, select, a[href^="#"]';
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.disabled;
  };
  /*COMMITTAL*/
  const found = [...document.querySelectorAll(selector)].filter(visible);
  const el = found.find(committal) || found[0];
  if (!el) return null;
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const name = n => n ? n.tagName.toLowerCase() + (n.id ? '#' + n.id : '')
                        + (typeof n.className === 'string' && n.className.trim() ? '.' + n.className.trim().split(/\\s+/)[0] : '') : 'nothing';
  return {x, y, label: name(el), text: (el.innerText || el.value || '').trim().slice(0, 40),
          blocked: !(top && (top === el || el.contains(top))), blocker: name(top),
          committal: committal(el)};
}""".replace("/*COMMITTAL*/", _COMMITTAL_JS)

# The next visible, unblocked control not clicked yet. Outermost elements only (a card and
# every child that inherits its pointer cursor are one control), and controls that lead
# away from the work (back, quit, close, reset, delete) come last so a click-through
# gets deeper before it backs out.
_NEXT_CONTROL_JS = """(seen) => {
  const selector = 'button, input[type=button], input[type=submit], [role=button], [onclick], summary, a[href^="#"], [tabindex]:not([tabindex="-1"])';
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.disabled;
  };
  /*COMMITTAL*/
  const candidates = [...document.querySelectorAll('body *')].slice(0, 3000)
    .filter(el => visible(el) && (el.matches(selector) || getComputedStyle(el).cursor === 'pointer'));
  const set = new Set(candidates);
  const outer = candidates.filter(el => { for (let p = el.parentElement; p; p = p.parentElement) if (set.has(p)) return false; return true; });
  const away = /\\b(back|quit|exit|forfeit|close|cancel|reset|clear|delete|remove all|new teams?)\\b/i;
  const info = el => {
    el.scrollIntoView({block: 'center', inline: 'center'});
    const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    const top = document.elementFromPoint(x, y);
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
    const name = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
      + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\\s+/)[0] : '');
    return {x, y, text, label: name, sig: name + '|' + text + '|' + Math.round(x / 40) + ',' + Math.round(y / 40),
            blocked: !(top && (top === el || el.contains(top))), committal: committal(el)};
  };
  const fresh = outer.map(info).filter(i => !seen.includes(i.sig));
  const open = fresh.filter(i => !i.blocked);
  const first = open.find(i => !away.test(i.text)) || open[0];
  if (first) return first;
  return fresh.length ? {sig: fresh[0].sig, skipped: true} : null;
}""".replace("/*COMMITTAL*/", _COMMITTAL_JS)

_STATE_JS = """() => {
  const h = s => { let a = 0; for (let i = 0; i < s.length; i++) a = (a * 31 + s.charCodeAt(i)) | 0; return a; };
  const canvases = [...document.querySelectorAll('canvas')].map(c => { try { return h(c.toDataURL()); } catch (e) { return 'x'; } });
  const values = [...document.querySelectorAll('input,textarea,select')].map(i => i.value + ':' + i.checked);
  return JSON.stringify([h(document.documentElement.outerHTML), location.href, document.title, canvases, values]);
}"""


_EXTERNAL_SYMPTOM = re.compile(
    r"is not defined|Cannot read propert(?:y|ies) of undefined|is not a function|"
    r"Failed to load resource|\$ is not|jQuery", re.IGNORECASE)


def _click_through(tab, steps: int, budget: float, failures: list, seen: list,
                   effects: list | None = None) -> tuple[int, int, list[str]]:
    """Click up to ``steps`` further controls; returns (clicked, changed, idle).

    ``changed`` is how many clicks visibly did something (the page changed, or a dialog,
    download, file chooser or new tab opened, which ``effects`` records). ``idle`` names
    the controls that must act but did nothing, for the report to mention.

    Stops when the page leaves its own address, when no new control is visible, or when
    the time budget is spent. A page error raised by a click is annotated with the
    control that caused it. Whatever the clicks break is collected by the page's own
    error listeners, so this only drives the page.
    """
    effects = effects if effects is not None else []
    deadline = time.monotonic() + budget
    start = tab.url.split("#")[0]
    clicked = changed = 0
    idle: list[str] = []
    while clicked < steps and time.monotonic() < deadline:
        try:
            nxt = tab.evaluate(_NEXT_CONTROL_JS, seen)
        except Exception:  # noqa: BLE001 -- the page navigated or closed
            break
        if not nxt:
            break
        seen.append(nxt["sig"])
        if nxt.get("skipped"):
            continue   # covered by something else; not a control to click
        known, effects_before = len(failures), len(effects)
        try:
            before = tab.evaluate(_STATE_JS)
        except Exception:  # noqa: BLE001
            before = None
        try:
            tab.mouse.click(nxt["x"], nxt["y"])
            tab.wait_for_timeout(150)
        except Exception:  # noqa: BLE001
            break
        clicked += 1
        cause = f" (after clicking {nxt['label']}" + (f" \"{nxt['text']}\"" if nxt["text"] else "") + ")"
        for i in range(known, len(failures)):
            if failures[i].startswith(("page error", "console error")):
                failures[i] += cause
        try:
            did_something = tab.evaluate(_STATE_JS) != before or len(effects) > effects_before
        except Exception:  # noqa: BLE001 -- the click navigated away: that is a change
            did_something = True
        if did_something:
            changed += 1
        elif nxt.get("committal"):
            idle.append(f"{nxt['label']}" + (f" \"{nxt['text']}\"" if nxt["text"] else ""))
        if tab.url.split("#")[0] != start:
            break
    return clicked, changed, idle


def _web_worker(root: Path, page: str, timeout: float, deny, out: dict,
                click_steps: int = CLICK_THROUGH_STEPS) -> None:
    from playwright.sync_api import sync_playwright

    failures, warnings, console = [], [], []
    external: list[str] = []
    external_scripts: list[str] = []
    dialogs: list[str] = []
    effects: list[str] = []   # visible effects that are not a change of the page: dialogs, downloads, new tabs
    cancelled = out.setdefault("cancelled", threading.Event())
    if cancelled.is_set():
        return
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(root, deny))
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    out["server"] = server   # so a timed-out check can still shut it down
    browser = None
    try:
        with sync_playwright() as p:
            if cancelled.is_set():
                return
            # A unique command-line marker identifies only this check's browser
            # tree, including a browser still starting when the timeout fires.
            out["browser_marker"] = f"--agent8088-smoke-check={uuid.uuid4().hex}"
            browser = p.chromium.launch(args=[out["browser_marker"]])
            out["browser_started"] = True
            if cancelled.is_set():
                return
            context = browser.new_context(accept_downloads=False, permissions=[],
                                          service_workers="block",
                                          viewport={"width": 1280, "height": 800})
            # WebSockets are not HTTP requests, so the route below cannot stop
            # them; close each one before it connects.
            block_websockets = getattr(context, "route_web_socket", None)
            if not callable(block_websockets):
                raise RuntimeError("Playwright lacks WebSocket interception; upgrade Playwright before checking pages")
            block_websockets("**/*", lambda ws: ws.close())

            def route(request_route):
                url = request_route.request.url
                if url.startswith((origin, "data:", "blob:", "about:")):
                    request_route.continue_()
                else:
                    if url not in external:
                        external.append(url)
                    if request_route.request.resource_type == "script" and url not in external_scripts:
                        external_scripts.append(url)
                    request_route.abort()

            context.route("**/*", route)
            tab = context.new_page()
            tab.set_default_timeout(timeout * 1000)
            # alert()/confirm() is a visible effect of a click; dismiss it so the
            # page does not hang waiting for an answer.
            tab.on("dialog", lambda dialog: (dialogs.append(dialog.message[:80]),
                                             effects.append("dialog"), dialog.dismiss()))
            tab.on("download", lambda download: effects.append("download"))
            tab.on("filechooser", lambda chooser: effects.append("file chooser"))
            tab.on("popup", lambda popup: effects.append("new tab"))

            def own(url: str) -> bool:
                return not url or url.startswith((origin, "data:", "blob:", "about:"))

            tab.on("pageerror", lambda error: failures.append(f"page error: {str(error)[:200]}"))

            def on_console(message):
                if message.type != "error":
                    return
                where = (message.location or {}).get("url", "")
                text = message.text[:200]
                console.append(f"error: {text}" + (f" ({where.replace(origin, '')})" if where and own(where) else ""))
                if own(where):
                    failures.append(f"console error: {text}")

            def on_response(response):
                if response.url.startswith(origin) and response.status >= 400:
                    failures.append(f"missing or failing file: {response.url.replace(origin, '')} "
                                    f"returned {response.status}")

            tab.on("console", on_console)
            tab.on("response", on_response)
            tab.on("requestfailed", lambda r: failures.append(
                f"request failed: {r.url.replace(origin, '')}") if r.url.startswith(origin) else None)
            try:
                tab.goto(f"{origin}/{page}", wait_until="load")
            except Exception as error:  # noqa: BLE001
                failures.append(f"the page did not load: {str(error).splitlines()[0][:160]}")
            else:
                tab.wait_for_timeout(300)
                control = tab.evaluate(_FIRST_CONTROL_JS)
                if control is None:
                    warnings.append("no visible button or control found to click")
                elif control["blocked"]:
                    failures.append(
                        f"the first control ({control['label']}"
                        + (f" \"{control['text']}\"" if control['text'] else "")
                        + f") is covered by {control['blocker']}, so a click never reaches it")
                else:
                    before = tab.evaluate(_STATE_JS)
                    effects_before = len(effects)
                    tab.mouse.click(control["x"], control["y"])
                    tab.wait_for_timeout(400)
                    try:
                        after = tab.evaluate(_STATE_JS)
                    except Exception:  # noqa: BLE001 -- the click navigated away: that is a change
                        after = None
                    described = (f"({control['label']}"
                                 + (f" \"{control['text']}\"" if control['text'] else "") + ")")
                    changed = 0 if after == before else 1
                    undecided = False   # a control that may do nothing by design; judged after the click-through
                    if after == before and dialogs:
                        warnings.append(f"the first control opened a popup: \"{dialogs[0]}\"")
                    elif after == before and len(effects) > effects_before:
                        changed = 1     # a download, a file chooser or a new tab is something happening
                    elif after == before and control.get("committal", True):
                        # A button that must act, and nothing happened: the page is not working.
                        failures.append(f"clicking the first control {described} changed nothing on the page")
                    elif after == before:
                        undecided = True
                    if after is not None and click_steps > 0:
                        _, more, idle = _click_through(
                            tab, click_steps, min(timeout * 0.5, 12.0), failures,
                            [f"{control['label']}|{control['text']}|"
                             f"{round(control['x'] / 40)},{round(control['y'] / 40)}"], effects)
                        changed += more
                        # Later controls can depend on what was clicked before, so they only warn.
                        warnings.extend(f"clicking {item} changed nothing on the page" for item in idle[:3])
                    if undecided:
                        if changed == 0:
                            failures.append(f"clicking the first control {described} changed nothing on the page,"
                                            " and no other control that was tried changed it either")
                        else:
                            warnings.append(f"the first control {described} changed nothing "
                                            f"({changed} other control{'s' if changed != 1 else ''} did)")
            if external_scripts:
                # A blocked script may explain a missing global, but cannot
                # prove that the application is correct. Fonts/images alone
                # never excuse a local JavaScript error.
                keep = []
                uncertain = []
                for item in failures:
                    if item.startswith(("page error", "console error")) and _EXTERNAL_SYMPTOM.search(item):
                        uncertain.append(item)
                        warnings.append(f"{item} (possibly caused by a blocked external script; not verified)")
                    else:
                        keep.append(item)
                failures[:] = keep
                if uncertain and not failures:
                    out["unavailable"] = "blocked external scripts prevented verification of JavaScript behavior"
            for url in external[:MAX_ITEMS]:
                warnings.append(f"blocked request to an external host: {url[:120]}")
            if len(external) > MAX_ITEMS:
                warnings.append(f"... and {len(external) - MAX_ITEMS} more external requests")
            out.update(failures=failures, warnings=warnings, console=console)
    except Exception as error:  # noqa: BLE001
        out["error"] = str(error).splitlines()[0][:200] if str(error) else type(error).__name__
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:  # noqa: BLE001
            pass
        server.shutdown()
        server.server_close()


def _abandon(out: dict, started: float) -> None:
    """A check that timed out must not leave its server or browser behind."""
    cancelled = out.get("cancelled")
    if cancelled is not None:
        cancelled.set()
    server = out.get("server")
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001
            pass
    marker = out.get("browser_marker")
    if not marker:
        return
    try:
        import psutil
        me = psutil.Process()
        for child in me.children(recursive=True):
            try:
                if marker in child.cmdline():
                    for sub_proc in child.children(recursive=True):
                        sub_proc.kill()
                    child.kill()
            except psutil.Error:
                continue
    except ImportError:
        pass


def check_web_page(path: Path, timeout: float = DEFAULT_TIMEOUT,
                   deny: Callable[[str], bool] | None = None,
                   click_steps: int | None = None) -> Report:
    path = Path(path)
    if not path.is_file():
        return Report("web", str(path), FAIL, ["the file does not exist"])
    if not chromium_available():
        return Report("web", str(path), UNAVAILABLE,
                      note="Chromium is not installed; run `playwright install chromium`")
    out: dict = {"cancelled": threading.Event()}
    # A fresh thread: Playwright's sync API refuses to run inside a running event loop.
    root = serve_root(path)
    page_url = path.relative_to(root).as_posix() if root in path.parents else path.name
    started = time.time()
    steps = CLICK_THROUGH_STEPS if click_steps is None else max(0, click_steps)
    worker = threading.Thread(target=_web_worker, args=(root, page_url, timeout, deny, out, steps),
                              daemon=True)
    worker.start()
    worker.join(timeout + 15)
    if worker.is_alive():
        _abandon(out, started)
        return Report("web", str(path), UNAVAILABLE, note=f"the browser check did not finish in {timeout + 15:.0f}s")
    if "error" in out:
        return Report("web", str(path), UNAVAILABLE, note=f"the browser could not run: {out['error']}")
    failures = list(dict.fromkeys(out.get("failures", [])))
    status = FAIL if failures else UNAVAILABLE if out.get("unavailable") else PASS
    return Report("web", str(path), status, failures, out.get("warnings", []), out.get("console", []),
                  note=out.get("unavailable", ""))


# --- scripts: static only --------------------------------------------------------------------------

_CRASH = re.compile(r"Traceback \(most recent call last\)|SyntaxError|ReferenceError|"
                    r"Cannot find module|ModuleNotFoundError|ImportError", re.IGNORECASE)
_EXIT_NONZERO = re.compile(r"Command exited with status ([1-9]\d*)")
# A script meant for a page (loaded by a <script> tag) fails under Node the moment it
# touches a browser global. That says nothing about the script, so it is not a crash.
_BROWSER_GLOBAL = re.compile(
    r"ReferenceError: (?:window|document|navigator|localStorage|sessionStorage|location|alert|"
    r"requestAnimationFrame|cancelAnimationFrame|HTMLElement|Image|Audio|AudioContext|Element|"
    r"customElements|screen|history) is not defined")


OUTPUT_CLIP = 400   # characters kept from each end of a script's start-up output


def clip_output(output: str, limit: int = OUTPUT_CLIP) -> str:
    """The first and last `limit` characters of an output, so a long one stays readable."""
    text = (output or "").strip()
    if len(text) <= 2 * limit:
        return text
    return text[:limit] + "\n[...]\n" + text[-limit:]


# The last line of a traceback or error that is about the place the script was started in
# (a read-only copy, an input or output path, a missing argument) and not about the script.
_ENVIRONMENT_FINAL = re.compile(
    r"^(?:PermissionError|FileNotFoundError|IsADirectoryError|NotADirectoryError|EOFError"
    r"|OSError: \[(?:Errno (?:1|2|13|30)|WinError (?:2|3|5|19|32))\]|Error: (?:EROFS|EACCES|ENOENT|EPERM))"
    r"|read-only file system|no such file or directory|permission denied|access is denied"
    r"|\[winerror (?:2|3|5|19|32)\]",
    re.IGNORECASE)
# The note the sandbox appends when a command tried to write where it may not. It is about the
# place the command ran in, never about the script, so it is not part of the error to judge.
_SANDBOX_NOTE = re.compile(r"\n?\[sandbox\][\s\S]*$")


def _final_line(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines()
             if line.strip() and not line.strip().startswith("Command exited")]
    return lines[-1] if lines else ""


def _run_briefly(path: Path, run: Callable[[str, int], str],
                 unavailable: list[str] | None = None,
                 captured: list[str] | None = None,
                 soft: list[str] | None = None) -> list[str]:
    """Start the script with --help in the sandbox; return crash findings.

    Only a crash on startup counts. Printing usage, finishing, or still running
    when the short clock ends are all fine, and a missing sandbox is no finding.
    `captured`, when given, receives the clipped output of a start that reported
    a crash, so a failure can be diagnosed from what the script actually printed.

    The start happens in a read-only copy with no arguments, so a script that does its
    work when started (builds a file, reads an input) can fail there for reasons that say
    nothing about it. With `soft` given, such a start (a bare non-zero exit with no
    error text, or an error about a path, permission or read-only file system) is
    reported there as "not verified" instead of as a crash. An error about the code itself
    (a syntax, import, name or type error) is a crash either way.
    """
    program = "python" if path.suffix.lower() in PYTHON_SUFFIXES else "node"
    output = str(run(f"{program} {shlex.quote(path.name)} --help", 10) or "")
    if "timed out" in output.lower():
        return []
    if program == "node" and _BROWSER_GLOBAL.search(output):
        if unavailable is not None:
            unavailable.append("parses; browser context is required; startup was not verified")
        return []
    crashed = _CRASH.search(output)
    exited = _EXIT_NONZERO.search(output)
    if crashed or (exited and "usage" not in output.lower()):
        last = [line for line in output.strip().splitlines() if line.strip()]
        if captured is not None:
            captured.append(clip_output(output))
        final = _final_line(_SANDBOX_NOTE.sub("", output))
        if soft is not None and ((not crashed) or _ENVIRONMENT_FINAL.search(final)):
            status = exited.group(1) if exited else "?"
            soft.append(f"it exited with status {status} when started without arguments in a read-only copy"
                        + (f" ({final[:100]})" if crashed else "") + "; whether it works was not verified")
            return []
        return [f"the script crashed when started: {last[-1][:160] if last else 'non-zero exit'}"]
    return []


# --- servers: does it actually come up --------------------------------------------------

# Patterns that LISTEN. Only these (plus a declared port, below) justify
# starting a script in the background: a framework import or a --port flag alone
# says "probably a server" but a client utility can carry either, and a false
# positive here means the harness starts a process it was never asked to start.
_LISTENS = re.compile(
    r"serve_forever\s*\("
    r"|\.listen\s*\("
    r"|\bapp\.run\s*\("
    r"|\buvicorn\.run\s*\("
    r"|\bgunicorn\b"
    r"|\b(?:HTTPServer|TCPServer|ThreadingHTTPServer|ThreadingTCPServer|UDPServer)\s*\("
    r"|\bcreateServer\s*\(", re.IGNORECASE)

# Weaker "this is meant to serve" signals: a server framework, or a --port
# argument. Worth reporting a server as unverified for, never worth starting it.
_SERVER_FRAMEWORKS = re.compile(
    r"\b(?:import|from)\s+(?:flask|fastapi|uvicorn|gunicorn|tornado|bottle|sanic|"
    r"cherrypy|aiohttp|starlette)\b"
    r"|\brequire\s*\(\s*['\"](?:express|koa|fastify|hono|polka|restify)['\"]"
    r"|\bfrom\s+['\"](?:express|koa|fastify|hono|polka|restify)['\"]"
    r"|\bimport\s+(?:express|koa|fastify|hono|polka|restify)\b"
    r"|add_argument\(\s*['\"]--port['\"]", re.IGNORECASE)

# Where a port number can hide in server source. Every alternative still has to
# land on a literal number: a port read from argv or the environment with no
# visible default stays unknown rather than guessed.
_PORT_HINTS = (
    re.compile(r"\bport\s*[=:]\s*(\d{2,5})\b", re.IGNORECASE),          # port=8000, PORT = 3000
    re.compile(r"\.listen\s*\(\s*(\d{2,5})\b"),                          # app.listen(3000)
    re.compile(r"['\"]--port['\"][^)\n]{0,80}?default\s*=\s*(\d{2,5})"),  # argparse --port default=8000
    re.compile(r"get(?:env)?\(\s*['\"]PORT['\"]\s*,\s*['\"]?(\d{2,5})", re.IGNORECASE),  # environ.get("PORT", 5000)
    re.compile(r"env\.PORT\b[^|\n]{0,20}\|\|\s*(\d{2,5})\b", re.IGNORECASE),            # process.env.PORT || 3000
    re.compile(r"\(\s*['\"][^'\"]*['\"]\s*,\s*(\d{2,5})\s*\)"),           # ("127.0.0.1", 8000), ('', 8000)
)


def looks_like_server(source: str) -> bool:
    """Whether the source is meant to serve connections somehow.

    A listening call, a server framework, or a declared --port argument
    counts. A file merely named serve.py does not: names are the model's to
    choose and prove nothing about the code.
    """
    return bool(_LISTENS.search(source) or _SERVER_FRAMEWORKS.search(source))


def server_port(source: str) -> int | None:
    """The port this source will listen on, or None when it cannot be known.

    The boot probe runs only when both a listening call and a concrete port
    number are visible in the source. A framework entry point that reads its
    port from an environment variable with no default, or .listen() with no
    number, gets None -- starting it would prove nothing about a port we
    could not name, so it is reported as unverified instead.
    """
    if not _LISTENS.search(source):
        return None
    for hint in _PORT_HINTS:
        found = hint.search(source)
        if found:
            port = int(found.group(1))
            if 1 <= port <= 65535:
                return port
    return None


# The probe itself: one program, run by the sandbox's own python, that starts
# the app, waits for the declared port, and stops the app. It travels
# base64-encoded because cmd.exe ends a command at its first newline and
# neither shell survives quoting a multi-line program -- the same trick the
# engine uses for its sandbox snippets. %r/%d are filled in by _boot_command;
# the snippet must therefore contain no other % character.
_BOOT_SNIPPET = """import os, signal, socket, subprocess, sys, threading, time
program, script, port, wait = %r, %r, %d, %r
try:
    proc = subprocess.Popen([program, script], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            **({"start_new_session": True}
                                if sys.platform != "win32" else {}))
except OSError as error:
    print("BOOT:NOTSTARTED " + str(error)[:160])
    raise SystemExit(0)
log = []
try:
    def _drain():
        # read1, not read: read(n) blocks until n bytes arrive, and a chatty
        # server would block on a full pipe instead of serving.
        try:
            while True:
                chunk = proc.stderr.read1(4096)
                if not chunk:
                    break
                log.append(chunk)
        except Exception:
            pass
    threading.Thread(target=_drain, daemon=True).start()
    verdict = "REFUSED"
    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            verdict = "DIED"
            break
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            verdict = "SERVING"
            break
        except OSError:
            time.sleep(0.25)
finally:
    # Whatever path got here -- serving, refused, dead, or an exception in
    # between -- the process this probe started must not survive it.
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    try:
        proc.stderr.close()
    except Exception:
        pass
tail = b"".join(log)[-400:].decode("utf-8", "replace").strip().splitlines()
print("BOOT:" + verdict + (": " + tail[-1][:160] if verdict == "DIED" and tail else ""))
"""


def _boot_command(program: str, script: str, port: int, wait: float) -> str:
    """One sandbox command that starts the app, waits for its port, and stops it.

    The stop is inside the command, in a finally, so the app cannot outlive the
    probe on any path the command itself can take; a timeout or cancellation on
    this side kills the command's whole process tree through the sandbox
    runner. That is why app_check holds no process of its own to leak.
    """
    code = _BOOT_SNIPPET % (program, script, port, wait)
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    loader = ("import base64;exec(compile(base64.b64decode("
              f"'{encoded}').decode('utf-8'),'<boot>','exec'))")
    return f'python -c "{loader}"'


def _boot_report(path: Path, program: str, port: int,
                 run: Callable[[str, int], str]) -> Report:
    """Start the script as a server in the sandbox and report whether it came up.

    Serving is a pass; a port that never opens, or a process that exits before
    serving, is a failure -- the run claimed to build a working server. A probe
    that could not run, timed out, or produced no verdict is "unavailable":
    serving was simply not verified, which is never the same as verified.
    """
    command = _boot_command(program, path.name, port, BOOT_WAIT)
    try:
        output = str(run(command, BOOT_RUN_TIMEOUT) or "")
    except Exception as error:  # noqa: BLE001 -- a broken runner is a probe limit, not the app's
        return Report("script", str(path), UNAVAILABLE,
                      note=f"the boot probe could not run: {str(error)[:120]}")
    if "BOOT:" in output:
        # A verdict is printed only after the finally, so cleanup already
        # happened; trust it over the runner's own timeout wording.
        line = output[output.index("BOOT:"):].splitlines()[0].strip()
    elif "timed out" in output.lower():
        return Report("script", str(path), UNAVAILABLE,
                      note="the boot probe did not finish; serving was not verified")
    else:
        line = ""
    if line.startswith("BOOT:SERVING"):
        return Report("script", str(path), PASS,
                      note=f"starts and accepts connections on 127.0.0.1:{port}")
    if line.startswith("BOOT:REFUSED"):
        return Report("script", str(path), FAIL,
                      [f"started but port {port} never accepted a connection "
                       f"within {BOOT_WAIT:.0f}s"])
    if line.startswith("BOOT:DIED"):
        detail = line[len("BOOT:DIED"):].strip(" :")
        return Report("script", str(path), FAIL,
                      ["the server exited before accepting connections on port "
                       f"{port}" + (f": {detail}" if detail else "")])
    if line.startswith("BOOT:NOTSTARTED"):
        detail = line[len("BOOT:NOTSTARTED"):].strip(" :")
        return Report("script", str(path), UNAVAILABLE,
                      note="the sandbox could not start it" + (f": {detail[:120]}" if detail else ""))
    return Report("script", str(path), UNAVAILABLE,
                  note="the boot probe produced no verdict; serving was not verified")


def check_script(path: Path, run: Callable[[str, int], str] | None = None) -> Report:
    path = Path(path)
    if not path.is_file():
        return Report("script", str(path), FAIL, ["the file does not exist"])
    suffix = path.suffix.lower()
    if suffix in PYTHON_SUFFIXES:
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            compile(source, str(path), "exec")
        except SyntaxError as error:
            return Report("script", str(path), FAIL,
                          [f"syntax error on line {error.lineno}: {error.msg}"])
        if run is not None:
            port = server_port(source)
            if port is not None:
                # A server is verified by booting it, not by asking it for
                # --help: the boot probe answers the question --help never could.
                return _boot_report(path, "python", port, run)
            captured: list[str] = []
            soft: list[str] = []
            crash = _run_briefly(path, run, captured=captured, soft=soft)
            if crash:
                return Report("script", str(path), FAIL, crash, output="".join(captured))
            if soft:
                return Report("script", str(path), UNAVAILABLE, note=soft[0], output="".join(captured), ask=True)
            if looks_like_server(source):
                return Report("script", str(path), UNAVAILABLE,
                              note="looks like a server but the port it will listen on "
                                   "could not be determined; boot not verified")
            return Report("script", str(path), PASS, note="compiles and starts without crashing")
        if server_port(source) is not None or looks_like_server(source):
            # No sandbox, so nothing was started: serving is unverified, not passed.
            return Report("script", str(path), UNAVAILABLE,
                          note="no sandbox available; the server was not executed")
        return Report("script", str(path), PASS, note="compiles; not executed")
    if suffix in NODE_SUFFIXES:
        node = shutil.which("node")
        if not node:
            return Report("script", str(path), UNAVAILABLE, note="node is not installed")
        try:
            done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            return Report("script", str(path), UNAVAILABLE, note=str(error)[:100])
        if done.returncode != 0:
            first = (done.stderr or done.stdout).strip().splitlines()
            return Report("script", str(path), FAIL, [f"syntax error: {first[-1][:160] if first else 'node --check failed'}"])
        if run is not None:
            source = path.read_text(encoding="utf-8", errors="replace")
            port = server_port(source)
            if port is not None:
                return _boot_report(path, "node", port, run)
            unavailable = []
            captured: list[str] = []
            soft: list[str] = []
            crash = _run_briefly(path, run, unavailable, captured, soft)
            if crash:
                return Report("script", str(path), FAIL, crash, output="".join(captured))
            if soft:
                return Report("script", str(path), UNAVAILABLE, note=soft[0], output="".join(captured), ask=True)
            if unavailable:
                return Report("script", str(path), UNAVAILABLE, note=unavailable[0])
            if looks_like_server(source):
                return Report("script", str(path), UNAVAILABLE,
                              note="looks like a server but the port it will listen on "
                                   "could not be determined; boot not verified")
            return Report("script", str(path), PASS, note="parses and starts without crashing")
        if run is None and (server_port(path.read_text(encoding="utf-8", errors="replace")) is not None):
            return Report("script", str(path), UNAVAILABLE,
                          note="no sandbox available; the server was not executed")
        return Report("script", str(path), PASS, note="parses; not executed")
    return Report("script", str(path), SKIPPED, note="not a script this check understands")


def check_target(path: Path, timeout: float = DEFAULT_TIMEOUT,
                 deny: Callable[[str], bool] | None = None,
                 run: Callable[[str, int], str] | None = None,
                 click_steps: int | None = None) -> Report:
    """Pick the check from what the path is: a page, a folder with an index page, a script."""
    path = Path(path)
    if path.is_dir():
        for name in ("index.html", "index.htm"):
            if (path / name).is_file():
                return check_web_page(path / name, timeout, deny, click_steps)
        return Report("web", str(path), SKIPPED, note="no index.html in this folder")
    suffix = path.suffix.lower()
    if suffix in WEB_SUFFIXES:
        return check_web_page(path, timeout, deny, click_steps)
    if suffix in PYTHON_SUFFIXES | NODE_SUFFIXES:
        return check_script(path, run)
    return Report("script", str(path), SKIPPED, note="not a page or script")


# --- which files a run produced are worth checking -----------------------------------------------

_HELPER_NAME = re.compile(r"^(?:tmp|temp|scratch|debug)(?:[_-].*)?$")


def is_helper_script(path: Path) -> bool:
    """A throwaway script the model wrote to inspect or patch its own work.

    A single leading underscore (_clean.py, _fix.js), or a name that is tmp, temp,
    scratch or debug alone or followed by _ or -, marks a helper (temperature.py does not). __init__.py and other dunder names are not helpers.
    Starting one with --help or syntax-checking it says nothing about the deliverable,
    and a helper that exits non-zero used to fail the run's smoke test.
    """
    stem = Path(path).stem.lower()
    if stem.startswith("__"):
        return False
    return (stem.startswith("_") and len(stem) > 1) or bool(_HELPER_NAME.match(stem))


def deliverable_targets(written: Iterable[str], roots: Iterable[Path], limit: int = 3) -> list[Path]:
    """Pages and runnable scripts this run wrote that exist, pages first, newest last.

    Tests, files under node_modules or a virtualenv, scratch dirs, and the model's own
    throwaway helpers (_fix.py, tmp_check.js) are skipped.
    """
    roots = [Path(r) for r in roots]
    found: list[Path] = []
    for raw in written:
        candidates = [Path(raw)] if Path(raw).is_absolute() else [r / raw for r in roots]
        path = next((c for c in candidates if c.is_file()), None)
        if path is None or path in found:
            continue
        parts = {part.lower() for part in path.parts}
        if parts & {"node_modules", ".venv", "venv", "__pycache__", "tests", "test", "__tests__"}:
            continue
        stem = path.stem.lower()
        if stem.startswith("test_") or stem.endswith("_test") or ".test" in path.name.lower():
            continue
        if is_helper_script(path):
            continue
        if path.suffix.lower() in WEB_SUFFIXES | PYTHON_SUFFIXES | NODE_SUFFIXES:
            found.append(path)
    pages = [p for p in found if p.suffix.lower() in WEB_SUFFIXES]
    scripts = [p for p in found if p.suffix.lower() not in WEB_SUFFIXES]
    return (pages + scripts)[:limit]
