"""Does the thing the run built actually work? A deterministic smoke test.

A web page is loaded in headless Chromium (Playwright) from a throwaway static
server bound to 127.0.0.1 that serves only the page's own folder. Requests to any
other host are intercepted and aborted, so untrusted page code cannot reach the
network, and nothing here loosens the SSRF rules or the browse_page gating. The
page fails the check on a page error, a console error from its own code, a
missing or failing own-origin asset, a first control that an overlay blocks, or a
first control whose click changes nothing. A request to an external host
(hotlinked fonts, images, CDN scripts) is only a warning.

A script gets a static check: it must compile (Python) or parse (Node). Nothing
is executed. Without Chromium the check reports "unavailable" instead of failing
the task.
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import threading
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
    return _chromium_available(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))


@functools.lru_cache(maxsize=4)
def _chromium_available(browsers_path: str) -> bool:
    """Probed on its own thread: the sync API starts an event loop, which must not
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
    return bool(found and found[0])


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


def _web_worker(root: Path, page: str, timeout: float, deny, out: dict) -> None:
    from playwright.sync_api import sync_playwright

    failures, warnings, console = [], [], []
    external: list[str] = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(root, deny))
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(accept_downloads=False, permissions=[],
                                          viewport={"width": 1280, "height": 800})

            def route(request_route):
                url = request_route.request.url
                if url.startswith((origin, "data:", "blob:", "about:")):
                    request_route.continue_()
                else:
                    if url not in external:
                        external.append(url)
                    request_route.abort()

            context.route("**/*", route)
            tab = context.new_page()
            tab.set_default_timeout(timeout * 1000)

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
                    if after == before:
                        failures.append(
                            f"clicking the first control ({control['label']}"
                            + (f" \"{control['text']}\"" if control['text'] else "")
                            + ") changed nothing on the page")
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


def check_web_page(path: Path, timeout: float = DEFAULT_TIMEOUT,
                   deny: Callable[[str], bool] | None = None) -> Report:
    path = Path(path)
    if not path.is_file():
        return Report("web", str(path), FAIL, ["the file does not exist"])
    if not chromium_available():
        return Report("web", str(path), UNAVAILABLE,
                      note="Chromium is not installed; run `playwright install chromium`")
    out: dict = {}
    # A fresh thread: Playwright's sync API refuses to run inside a running event loop.
    worker = threading.Thread(target=_web_worker, args=(path.parent, path.name, timeout, deny, out),
                              daemon=True)
    worker.start()
    worker.join(timeout + 15)
    if worker.is_alive():
        return Report("web", str(path), UNAVAILABLE, note=f"the browser check did not finish in {timeout + 15:.0f}s")
    if "error" in out:
        return Report("web", str(path), UNAVAILABLE, note=f"the browser could not run: {out['error']}")
    failures = list(dict.fromkeys(out.get("failures", [])))
    status = FAIL if failures else PASS
    return Report("web", str(path), status, failures, out.get("warnings", []), out.get("console", []))


# --- scripts: static only --------------------------------------------------------------------------

def check_script(path: Path) -> Report:
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
        return Report("script", str(path), PASS, note="parses; not executed")
    return Report("script", str(path), SKIPPED, note="not a script this check understands")


def check_target(path: Path, timeout: float = DEFAULT_TIMEOUT,
                 deny: Callable[[str], bool] | None = None) -> Report:
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
        return check_script(path)
    return Report("script", str(path), SKIPPED, note="not a page or script")


# --- which files a run produced are worth checking -----------------------------------------------

def deliverable_targets(written: Iterable[str], roots: Iterable[Path], limit: int = 3) -> list[Path]:
    """Pages and runnable scripts this run wrote that exist, pages first, newest last.

    Tests, files under node_modules or a virtualenv, and scratch dirs are skipped.
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
        if path.suffix.lower() in WEB_SUFFIXES | PYTHON_SUFFIXES | NODE_SUFFIXES:
            found.append(path)
    pages = [p for p in found if p.suffix.lower() in WEB_SUFFIXES]
    scripts = [p for p in found if p.suffix.lower() not in WEB_SUFFIXES]
    return (pages + scripts)[:limit]
