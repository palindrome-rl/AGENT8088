"""Render a local visual file in headless Chromium and measure the picture.

Used by the `screenshot_page` tool. It reuses check_app's guarded loader: a throwaway static server
on 127.0.0.1 that serves only the file's own folder (app_check.serve_root and app_check._handler),
and a browser context that aborts every request to another host and closes every WebSocket.

A page (.html, .htm) is loaded directly. An image-type file (.svg and the raster formats) is shown
through an <img> element on a tiny wrapper page served from the same throwaway server, so scripts,
event handlers and external references inside it never run or load.

Everything reported here is deterministic: no model is involved.
"""
from __future__ import annotations

import http.server
import json
import threading
import time
import uuid
import xml.parsers.expat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent8088 import app_check

PAGE_SUFFIXES = frozenset({".html", ".htm"})
IMAGE_SUFFIXES = frozenset({".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
XML_SUFFIXES = frozenset({".svg", ".xml"})
RENDERABLE = PAGE_SUFFIXES | IMAGE_SUFFIXES

# A pixel counts as different (from the background, or from another render) when any colour
# channel moves by more than this. Absorbs anti-aliasing and JPEG-like noise, nothing more.
CHANNEL_TOLERANCE = 24
ANIMATION_GAP_MS = 400        # second capture after this many milliseconds, to tell a moving page
ANIMATION_SHARE = 0.002       # differing share between the two captures that counts as moving
_WRAPPER = "__agent8088_render__.html"


def chromium_executable():
    """Indirection so tests can simulate a machine without Chromium."""
    return app_check.chromium_executable()


@dataclass
class Render:
    status: str                          # "ok", "unavailable" or "error"
    note: str = ""
    png: Path | None = None
    page_errors: list = field(default_factory=list)
    console_errors: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    animating: bool = False


# --- well-formedness -----------------------------------------------------------------------------------

class _Refused(Exception):
    pass


def xml_problem(path: Path) -> str | None:
    """Why an XML-based file (such as SVG) is not well formed, or None.

    Parsed with expat and no external resolution; a DOCTYPE or entity declaration is refused
    outright rather than expanded."""
    parser = xml.parsers.expat.ParserCreate()

    def refuse_doctype(*_args):
        raise _Refused("it declares a DOCTYPE, which is refused (no entity or external declarations)")

    def refuse_entity(*_args):
        raise _Refused("it declares an entity, which is refused")

    parser.StartDoctypeDeclHandler = refuse_doctype
    parser.EntityDeclHandler = refuse_entity
    parser.ExternalEntityRefHandler = lambda *_a: False
    try:
        parser.Parse(path.read_bytes(), True)
    except _Refused as exc:
        return str(exc)
    except xml.parsers.expat.ExpatError as exc:
        return f"line {exc.lineno}, column {exc.offset + 1}: {xml.parsers.expat.ErrorString(exc.code)}"
    except OSError as exc:
        return f"could not be read: {exc.strerror or exc}"
    return None


# --- pixels --------------------------------------------------------------------------------------------

def _pixels(img):
    """Pixel values; get_flattened_data on Pillow 12+, getdata before it."""
    return (getattr(img, "get_flattened_data", None) or img.getdata)()


def _load(path):
    from PIL import Image
    with Image.open(path) as img:
        return img.convert("RGB")


def _differs(a, b) -> bool:
    return any(abs(x - y) > CHANNEL_TOLERANCE for x, y in zip(a, b))


def _background(img):
    """The most common colour along the image's border."""
    from collections import Counter
    w, h = img.size
    px = img.load()
    border = [px[x, 0] for x in range(w)] + [px[x, h - 1] for x in range(w)]
    border += [px[0, y] for y in range(h)] + [px[w - 1, y] for y in range(h)]
    return Counter(border).most_common(1)[0][0]


def _sampled(img, limit=160_000):
    """The image, downscaled so that measuring stays fast on a large full-page capture."""
    w, h = img.size
    if w * h <= limit:
        return img
    scale = (limit / (w * h)) ** 0.5
    from PIL import Image
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.NEAREST)


def ink_share(path) -> float:
    """Share of pixels that are not the background colour (0 for a blank render)."""
    img = _sampled(_load(path))
    bg = _background(img)
    data = list(_pixels(img))
    return sum(1 for p in data if _differs(p, bg)) / len(data)


def diff_share(path_a, path_b) -> float:
    """Share of pixels that differ between two renders (compared at the first one's size)."""
    a = _sampled(_load(path_a))
    b = _load(path_b)
    if b.size != a.size:
        from PIL import Image
        b = b.resize(a.size, Image.NEAREST)
    pairs = zip(_pixels(a), _pixels(b))
    total = a.size[0] * a.size[1]
    return sum(1 for p, q in pairs if _differs(p, q)) / total


# --- the browser ---------------------------------------------------------------------------------------

def _wrapper_html(relative: str) -> str:
    src = json.dumps("/" + relative.lstrip("/"))
    return ("<!doctype html><html><head><meta charset='utf-8'><style>html,body{margin:0;background:#fff}"
            "img{display:block;max-width:100%}</style></head><body>"
            f"<img alt='' src={src}></body></html>")


def _worker(root: Path, relative: str, as_image: bool, out_png: Path, width: int, height: int,
            wait_ms: int, full_page: bool, timeout: float, deny, out: dict, executable) -> None:
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, blocked = [], [], []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app_check._handler(root, deny))
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out["server"] = server
    wrapper_url = f"{origin}/{_WRAPPER}"
    try:
        with sync_playwright() as p:
            out["browser_marker"] = f"--agent8088-render={uuid.uuid4().hex}"
            browser = p.chromium.launch(executable_path=executable, args=[out["browser_marker"]])
            try:
                context = browser.new_context(accept_downloads=False, permissions=[], service_workers="block",
                                              viewport={"width": width, "height": height})
                context.route_web_socket("**/*", lambda ws: ws.close())

                def route(request_route):
                    url = request_route.request.url
                    if as_image and url.split("?", 1)[0] == wrapper_url:
                        request_route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                              body=_wrapper_html(relative))
                    elif url.startswith((origin, "data:", "blob:", "about:")):
                        request_route.continue_()
                    else:
                        if url not in blocked:
                            blocked.append(url)
                        request_route.abort()

                context.route("**/*", route)
                tab = context.new_page()
                tab.set_default_timeout(timeout * 1000)
                tab.on("pageerror", lambda error: page_errors.append(str(error).strip()[:200]))

                def on_console(message):
                    if message.type == "error" and not message.text.startswith("Failed to load resource"):
                        console_errors.append(message.text.strip()[:200])

                tab.on("console", on_console)
                tab.on("dialog", lambda dialog: dialog.dismiss())
                tab.goto(wrapper_url if as_image else f"{origin}/{relative}", wait_until="load")
                if wait_ms:
                    tab.wait_for_timeout(wait_ms)
                tab.screenshot(path=str(out_png), full_page=full_page)
                second = out_png.with_name(out_png.stem + ".motion.png")
                tab.wait_for_timeout(ANIMATION_GAP_MS)
                tab.screenshot(path=str(second), full_page=full_page)
                try:
                    out["animating"] = diff_share(out_png, second) > ANIMATION_SHARE
                finally:
                    second.unlink(missing_ok=True)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 -- reported as an unavailable render
        out["error"] = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
    finally:
        out.update(page_errors=page_errors, console_errors=console_errors, blocked=blocked)
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001
            pass


def render_file(path: Path, out_png: Path, *, width: int, height: int, wait_ms: int, full_page: bool,
                timeout: float, deny: Callable[[str], bool] | None = None) -> Render:
    """Render `path` to `out_png`. Never raises: an environment problem is an "unavailable" Render."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in RENDERABLE:
        return Render("error", note=f"cannot render {suffix or 'a file with no extension'} files")
    executable = chromium_executable()
    if executable is None:
        return Render("unavailable", note="Chromium is not installed for Agent8088, so nothing could be rendered; "
                                          "install it with `python -m playwright install chromium`")
    root = app_check.serve_root(path) if suffix in PAGE_SUFFIXES else path.parent
    relative = path.relative_to(root).as_posix() if root in path.parents else path.name
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out: dict = {}
    worker = threading.Thread(target=_worker, daemon=True, args=(
        root, relative, suffix in IMAGE_SUFFIXES, out_png, width, height, wait_ms, full_page, timeout, deny,
        out, executable))
    started = time.time()
    worker.start()
    worker.join(timeout + 15)
    if worker.is_alive():
        app_check._abandon(out, started)
        return Render("unavailable", note=f"the render did not finish in {timeout + 15:.0f}s")
    if "error" in out or not out_png.is_file():
        return Render("unavailable", note=f"the browser could not render the file: {out.get('error', 'no image')}",
                      page_errors=out.get("page_errors", []), console_errors=out.get("console_errors", []),
                      blocked=out.get("blocked", []))
    return Render("ok", png=out_png, page_errors=out["page_errors"], console_errors=out["console_errors"],
                  blocked=out["blocked"], animating=bool(out.get("animating")))
