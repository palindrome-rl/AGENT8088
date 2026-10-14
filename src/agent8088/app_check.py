"""Does the thing the run built actually work? A deterministic smoke test.

A web page is loaded in headless Chromium (Playwright) from a throwaway static
server bound to 127.0.0.1 that serves only the page's own folder. Requests to any
other host are intercepted and aborted, so untrusted page code cannot reach the
network, and nothing here loosens the SSRF rules or the browse_page gating. The
page fails the check on a page error, a console error from its own code, a
missing or failing own-origin asset, a first control that an overlay blocks, or a
first control whose click changes nothing. A request to an external host
(hotlinked fonts, images, CDN scripts) is only a warning.

A script must compile (Python) or parse (Node). When a read-only sandbox runner
is supplied it also gets a brief startup check there. Without Chromium the page
check reports "unavailable" instead of failing the task. Dependency errors from
blocked external scripts are inconclusive, never proof that the page passed.
"""

from __future__ import annotations

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

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    def summary(self) -> str:
        name = Path(self.target).name or self.target
        if self.status == PASS:
            tail = f" ({len(self.warnings)} warning{'s' if len(self.warnings) != 1 else ''})" if self.warnings else ""
            return f"{name}: smoke test passed{tail}"
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

_FIRST_CONTROL_JS = """() => {
  const selector = 'button, input[type=button], input[type=submit], [role=button], [onclick], summary, select, a[href^="#"]';
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.disabled;
  };
  const el = [...document.querySelectorAll(selector)].find(visible);
  if (!el) return null;
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const name = n => n ? n.tagName.toLowerCase() + (n.id ? '#' + n.id : '')
                        + (typeof n.className === 'string' && n.className.trim() ? '.' + n.className.trim().split(/\\s+/)[0] : '') : 'nothing';
  return {x, y, label: name(el), text: (el.innerText || el.value || '').trim().slice(0, 40),
          blocked: !(top && (top === el || el.contains(top))), blocker: name(top)};
}"""

_STATE_JS = """() => {
  const h = s => { let a = 0; for (let i = 0; i < s.length; i++) a = (a * 31 + s.charCodeAt(i)) | 0; return a; };
  const canvases = [...document.querySelectorAll('canvas')].map(c => { try { return h(c.toDataURL()); } catch (e) { return 'x'; } });
  const values = [...document.querySelectorAll('input,textarea,select')].map(i => i.value + ':' + i.checked);
  return JSON.stringify([h(document.documentElement.outerHTML), location.href, document.title, canvases, values]);
}"""


_EXTERNAL_SYMPTOM = re.compile(
    r"is not defined|Cannot read propert(?:y|ies) of undefined|is not a function|"
    r"Failed to load resource|\$ is not|jQuery", re.IGNORECASE)


def _web_worker(root: Path, page: str, timeout: float, deny, out: dict) -> None:
    from playwright.sync_api import sync_playwright

    failures, warnings, console = [], [], []
    external: list[str] = []
    external_scripts: list[str] = []
    dialogs: list[str] = []
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
            tab.on("dialog", lambda dialog: (dialogs.append(dialog.message[:80]), dialog.dismiss()))

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
                    tab.mouse.click(control["x"], control["y"])
                    tab.wait_for_timeout(400)
                    try:
                        after = tab.evaluate(_STATE_JS)
                    except Exception:  # noqa: BLE001 -- the click navigated away: that is a change
                        after = None
                    if after == before and dialogs:
                        warnings.append(f"the first control opened a popup: \"{dialogs[0]}\"")
                    elif after == before:
                        failures.append(
                            f"clicking the first control ({control['label']}"
                            + (f" \"{control['text']}\"" if control['text'] else "")
                            + ") changed nothing on the page")
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
                   deny: Callable[[str], bool] | None = None) -> Report:
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
    worker = threading.Thread(target=_web_worker, args=(root, page_url, timeout, deny, out),
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


def _run_briefly(path: Path, run: Callable[[str, int], str],
                 unavailable: list[str] | None = None) -> list[str]:
    """Start the script with --help in the sandbox; return crash findings.

    Only a crash on startup counts. Printing usage, finishing, or still running
    when the short clock ends are all fine, and a missing sandbox is no finding.
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
        return [f"the script crashed when started: {last[-1][:160] if last else 'non-zero exit'}"]
    return []


def check_script(path: Path, run: Callable[[str, int], str] | None = None) -> Report:
    path = Path(path)
    if not path.is_file():
        return Report("script", str(path), FAIL, ["the file does not exist"])
    suffix = path.suffix.lower()
    if suffix in PYTHON_SUFFIXES:
        try:
            compile(path.read_text(encoding="utf-8", errors="replace"), str(path), "exec")
        except SyntaxError as error:
            return Report("script", str(path), FAIL,
                          [f"syntax error on line {error.lineno}: {error.msg}"])
        if run is not None:
            crash = _run_briefly(path, run)
            if crash:
                return Report("script", str(path), FAIL, crash)
            return Report("script", str(path), PASS, note="compiles and starts without crashing")
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
            unavailable = []
            crash = _run_briefly(path, run, unavailable)
            if crash:
                return Report("script", str(path), FAIL, crash)
            if unavailable:
                return Report("script", str(path), UNAVAILABLE, note=unavailable[0])
            return Report("script", str(path), PASS, note="parses and starts without crashing")
        return Report("script", str(path), PASS, note="parses; not executed")
    return Report("script", str(path), SKIPPED, note="not a script this check understands")


def check_target(path: Path, timeout: float = DEFAULT_TIMEOUT,
                 deny: Callable[[str], bool] | None = None,
                 run: Callable[[str, int], str] | None = None) -> Report:
    """Pick the check from what the path is: a page, a folder with an index page, a script."""
    path = Path(path)
    if path.is_dir():
        for name in ("index.html", "index.htm"):
            if (path / name).is_file():
                return check_web_page(path / name, timeout, deny)
        return Report("web", str(path), SKIPPED, note="no index.html in this folder")
    suffix = path.suffix.lower()
    if suffix in WEB_SUFFIXES:
        return check_web_page(path, timeout, deny)
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
