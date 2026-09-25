"""Optical character recognition for models that cannot see.

A file path in, text out. Nothing here knows about models, sessions or
routing -- callers decide *whether* OCR is wanted (see
`model_catalog.vision_capable`); this module only answers *what does it say*.

Engine choice, recorded here because the trade-offs are not obvious from the
imports: recognition is RapidOCR on ONNX Runtime, and PDF pages are
rasterized with pypdfium2. Both are Apache-2.0/BSD, ship as pip wheels with
no system binary and no second inference server, and add roughly 30MB to an
install with no torch anywhere in the tree. In short, Surya needs vllm or
llama.cpp alongside and its weights are not redistributable by an MIT project,
PyMuPDF is AGPL, and Tesseract would put a
per-OS system binary back into the installer.

Recognition runs out of process for the same reason document extraction does:
an attachment is untrusted input reaching a native parser, so it gets a
timeout and a memory ceiling of its own rather than the server's thread.
"""
from collections import OrderedDict
from pathlib import Path
from threading import RLock
import subprocess
import sys

_cache = OrderedDict()
_lock = RLock()
_CACHE_CHARS = 4 * 1024 * 1024

OCR_TIMEOUT_SECONDS = 180
MAX_OCR_BYTES = 25 * 1024 * 1024
MAX_OCR_PAGES = 20
# 72 DPI is the PDF default and too coarse for recognition; 2.5x lands near
# 180 DPI, which reads reliably without the memory cost of a 300 DPI bitmap.
RENDER_SCALE = 2.5

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
OCR_SUFFIXES = IMAGE_SUFFIXES | {".pdf"}

_MISSING = ("OCR is not installed. Install the extra with: "
            'pip install -e ".[ocr]"')

_engine_instance = None


def _import_engines():
    """Both third-party engines, imported lazily.

    Kept behind a function so an install without the [ocr] extra reports OCR
    as unavailable instead of failing at import time -- every other module
    imports this one unconditionally.
    """
    from rapidocr import RapidOCR
    import pypdfium2
    return RapidOCR, pypdfium2


def available() -> bool:
    """Whether recognition can run at all on this install."""
    try:
        _import_engines()
    except Exception:
        return False
    return True


def _engine():
    """The RapidOCR instance, built once. Model load costs seconds."""
    global _engine_instance
    if _engine_instance is None:
        RapidOCR, _ = _import_engines()
        _engine_instance = RapidOCR()
    return _engine_instance


def _pdfium():
    _, pypdfium2 = _import_engines()
    return pypdfium2


def needs_ocr(path) -> bool:
    """Whether this file has no text source other than recognition.

    True for images only. A PDF is an OCR candidate solely once text
    extraction has come up empty, and that judgement belongs to the caller
    that ran the extraction -- a text-bearing PDF must keep going through
    document_read, which is both faster and lossless.
    """
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


# --- recognition, worker side ----------------------------------------------

def _recognised_lines(result) -> list:
    """Text lines out of a RapidOCR result, which is empty-ish in two ways:
    a None result when nothing was detected, or a result whose txts is None."""
    lines = getattr(result, "txts", None) or []
    return [str(line).strip() for line in lines if str(line).strip()]


def _image_text(path) -> str:
    return "\n".join(_recognised_lines(_engine()(str(path))))


def _pdf_text(path, max_pages=MAX_OCR_PAGES) -> str:
    document = _pdfium().PdfDocument(str(path))
    try:
        total = len(document)
        pages = []
        for index in range(min(total, max_pages)):
            image = document[index].render(scale=RENDER_SCALE).to_pil()
            body = "\n".join(_recognised_lines(_engine()(image)))
            pages.append(f"## Page {index + 1}\n{body}" if body
                         else f"## Page {index + 1}\n(no text recognised on this page)")
        if total > max_pages:
            # Say so in the output rather than truncating silently: a model
            # asked to summarise "the document" must know it saw part of it.
            pages.append(f"[{total - max_pages} further pages were not read; "
                         f"OCR stops at {max_pages} pages]")
        return "\n\n".join(pages)
    finally:
        try:
            document.close()
        except Exception:
            pass


# --- public API, caller side ------------------------------------------------

def _cache_key(path: Path):
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


def text_for(path, *, max_pages=MAX_OCR_PAGES) -> str:
    """Recognised text for one image or scanned PDF.

    Raises ValueError with a message meant for the user (and for the model,
    which is told when an attachment could not be read) rather than returning
    an empty string: silence here looks identical to a blank page.
    """
    path = Path(path).resolve()
    if not available():
        raise ValueError(_MISSING)
    suffix = path.suffix.lower()
    if suffix not in OCR_SUFFIXES:
        raise ValueError(f"{suffix or '(no extension)'} cannot be read by OCR")
    if not path.is_file():
        raise ValueError(f"File not found: {path.name}")
    if path.stat().st_size > MAX_OCR_BYTES:
        raise ValueError(f"File is too large for OCR (limit: {MAX_OCR_BYTES // (1024 * 1024)}MB)")

    key = _cache_key(path)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    argv = [sys.executable, "-m", "agent8088.ocr", "--extract", str(path),
            "--max-pages", str(max_pages)]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=OCR_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise ValueError(f"OCR timed out after {OCR_TIMEOUT_SECONDS}s on {path.name}")
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise ValueError(f"OCR failed on {path.name}: {detail}")
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError(f"OCR found no readable text in {path.name}")

    with _lock:
        _cache[key] = text
        while sum(len(value) for value in _cache.values()) > _CACHE_CHARS:
            _cache.popitem(last=False)
    return text


def discard(path) -> None:
    """Drop cached text for a file, for when an upload is deleted."""
    resolved = str(Path(path).resolve())
    with _lock:
        for key in [key for key in _cache if key[0] == resolved]:
            del _cache[key]


if __name__ == "__main__":
    import argparse

    from .document_access import _limit_worker_memory

    parser = argparse.ArgumentParser(prog="agent8088.ocr")
    parser.add_argument("--extract", required=True)
    parser.add_argument("--max-pages", type=int, default=MAX_OCR_PAGES)
    options = parser.parse_args()

    _limit_worker_memory()
    source = Path(options.extract)
    output = (_pdf_text(source, max_pages=options.max_pages)
              if source.suffix.lower() == ".pdf" else _image_text(source))
    sys.stdout.buffer.write((output or "").encode("utf-8"))
