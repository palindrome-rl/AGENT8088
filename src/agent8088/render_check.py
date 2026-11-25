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
    layout: dict = field(default_factory=dict)


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


# --- small layout notes --------------------------------------------------------------------------------

# Run in the page after the screenshot. Three plain facts about the window, nothing about taste: whether the page is larger than the
# window, text that is hard to read against its background (WCAG contrast under 3:1, skipped where the background is an image or
# a gradient), and text under 10 px. Only the text of the page itself is seen: text drawn on a canvas is not.
LAYOUT_PROBE = r"""() => {
  const de = document.documentElement;
  const out = {scrollW: de.scrollWidth, scrollH: de.scrollHeight, vw: window.innerWidth, vh: window.innerHeight, low: [], lowCount: 0, tiny: 0};
  const parse = c => { const m = (c || '').match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(parseFloat);
                       return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = c => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e);
                         if (s.backgroundImage && s.backgroundImage !== 'none') return null;
                         const c = parse(s.backgroundColor); if (c && c.a > 0.5) return c; }
                       return {r: 255, g: 255, b: 255, a: 1}; };
  const walker = document.createTreeWalker(document.body || de, NodeFilter.SHOW_TEXT);
  let seen = 0;
  while (walker.nextNode() && seen < 600) {
    const n = walker.currentNode, t = (n.nodeValue || '').trim(), el = n.parentElement;
    if (t.length < 2 || !el || /^(SCRIPT|STYLE|NOSCRIPT)$/.test(el.tagName)) continue;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0 || r.width < 1 || r.height < 1) continue;
    seen++;
    if (parseFloat(s.fontSize) < 10) out.tiny++;
    const fg = parse(s.color), bg = bgOf(el);
    if (!fg || !bg) continue;
    const l1 = lum(fg), l2 = lum(bg), ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    if (ratio < 3) { out.lowCount++; if (out.low.length < 3) out.low.push({t: t.slice(0, 24), r: Math.round(ratio * 10) / 10}); }
  }
  return out;
}"""


def layout_note(layout) -> str:
    """The probe's findings as one short sentence, or "" when there is nothing to say."""
    if not isinstance(layout, dict) or not layout:
        return ""
    parts = []
    width, height = int(layout.get("vw") or 0), int(layout.get("vh") or 0)
    scroll_w, scroll_h = int(layout.get("scrollW") or 0), int(layout.get("scrollH") or 0)
    if width and height and (scroll_w > width + 2 or scroll_h > height + 2):
        parts.append(f"the page is larger than the window ({scroll_w} x {scroll_h} in {width} x {height}), so it scrolls")
    count = int(layout.get("lowCount") or 0)
    if count:
        example = (layout.get("low") or [{}])[0]
        sample = f' (for example "{example.get("t", "")}" at {example.get("r", "")}:1)' if example.get("t") else ""
        parts.append(f"{count} text item{'s' if count != 1 else ''} hard to read against the background{sample}")
    tiny = int(layout.get("tiny") or 0)
    if tiny:
        parts.append(f"{tiny} text item{'s' if tiny != 1 else ''} under 10 px")
    return "; ".join(parts)


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
                if not as_image:
                    try:
                        out["layout"] = tab.evaluate(LAYOUT_PROBE)
                    except Exception:  # noqa: BLE001 -- notes are optional; a page that blocks the probe just has none
                        out["layout"] = {}
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
                  blocked=out["blocked"], animating=bool(out.get("animating")), layout=out.get("layout") or {})
