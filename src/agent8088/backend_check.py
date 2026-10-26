"""Backend check: does the project's own server start, and do the endpoints its page uses answer?

The page check (app_check) serves static files only, so a page that loads its data from its own server (`fetch('/api/pokemon')`)
passes without that server ever running, and the verdict says only "the HTML loaded". This check is optional (off by default,
`backend_check=1` in config.txt) and fills ONE separate field, "backend check". It never changes the verification level.

    off              the setting is off (nothing is recorded)
    not applicable   the project has no server of its own that could be found
    unavailable      it could not be checked here: port in use, a dependency or build step the sandbox lacks, a write the
                     read-only sandbox refuses, no sandbox launcher on this platform, the server never answered in time
    passed           the server started, and every GET endpoint the page uses answered 2xx or 3xx
    failed           the project's own code failed: the server died on its own error, stopped answering, or an endpoint the
                     page uses returned 4xx or 5xx

Only evidence that comes from the project's own code is a "failed"; everything about the machine is "unavailable". The server is
started through a launcher the engine supplies (the sandbox wrapper, kept alive by this module while the endpoints are probed and
stopped afterwards), so nothing here runs a generated server outside the sandbox. A port that is already in use is never
tested: that would verify somebody else's server.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

OFF, NOT_APPLICABLE, UNAVAILABLE, PASSED, FAILED = "off", "not applicable", "unavailable", "passed", "failed"
STARTUP_SECONDS = 30.0
PROBE_SECONDS = 6.0
MAX_ENDPOINTS = 8
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "pylibs", "tests", "test", "edge_profile"}


@dataclass
class Entry:
    kind: str                    # "node" or "python"
    script: Path
    cwd: Path
    port: int | None = None      # a port written in the source (it ignores ours)
    port_arg: bool = False       # `python server.py [port]`


@dataclass
class BackendReport:
    status: str
    detail: str = ""
    endpoints: list = field(default_factory=list)    # [{"path", "status"}]
    server: str = ""

    def summary(self) -> str:
        text = self.detail or self.status
        if self.server:
            text += f" [{self.server}]"
        return text


# --- finding the server -----------------------------------------------------------------------------

_PY_SERVER = re.compile(r"Flask\(|FastAPI\(|http\.server|socketserver|uvicorn|aiohttp|web\.run_app|app\.run\(|serve_forever|"
                        r"HTTPServer\(|django", re.IGNORECASE)
_JS_SERVER = re.compile(r"\.listen\(|createServer\(|express\(|fastify|http\.Server|Bun\.serve|Deno\.serve|serve\(", re.IGNORECASE)
_LITERAL_PORT = re.compile(r"(?:\bport\s*[=:]\s*|\blisten\(\s*)(\d{4,5})\b", re.IGNORECASE)


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def find_entry(root: Path) -> Entry | None:
    """The project's own server: a `npm start` script, else a conventional server file that really starts one.

    Looked for in the served folder and up to three folders above it (the page may sit in public/)."""
    folder = Path(root)
    for _ in range(4):
        found = _entry_in(folder)
        if found:
            return found
        if folder.parent == folder:
            break
        folder = folder.parent
    return None


def _entry_in(folder: Path) -> Entry | None:
    package = folder / "package.json"
    if package.is_file():
        try:
            start = (json.loads(_read(package)).get("scripts") or {}).get("start", "")
        except (ValueError, AttributeError):
            start = ""
        match = re.search(r"\bnode\s+(\S+\.m?js)\b", str(start))
        if match and (folder / match.group(1)).is_file():
            return _make_entry("node", folder / match.group(1), folder)
    for name in ("server.js", "server.mjs", "start-server.js", "index.js", "app.js"):
        path = folder / name
        if path.is_file() and (name.startswith(("server", "start")) or _JS_SERVER.search(_read(path))) and _JS_SERVER.search(_read(path)):
            return _make_entry("node", path, folder)
    for name in ("server.py", "app.py", "main.py", "run.py"):
        path = folder / name
        if path.is_file() and _PY_SERVER.search(_read(path)):
            return _make_entry("python", path, folder)
    return None


def _make_entry(kind: str, script: Path, cwd: Path) -> Entry:
    text = _read(script)
    match = _LITERAL_PORT.search(text)
    uses_env = bool(re.search(r"process\.env\.PORT|environ(?:\.get)?\s*[\[(]\s*['\"]PORT", text, re.IGNORECASE))
    return Entry(kind, script, cwd, int(match.group(1)) if match and not uses_env else None,
                 port_arg=(kind == "python" and "sys.argv" in text and not match and not uses_env))


# --- the endpoints the page uses --------------------------------------------------------------------

_FETCH = re.compile(r"""\bfetch\(\s*(['"`])(/[^'"`\s${}?#]*)(?:\?[^'"`]*)?\1\s*(?:,\s*\{(.{0,200}?)\})?""", re.DOTALL)
_AXIOS = re.compile(r"""(?:\baxios|\$|\bjQuery)\.(?:get|getJSON)\(\s*(['"`])(/[^'"`\s${}?#]*)""")
_XHR = re.compile(r"""\.open\(\s*['"]GET['"]\s*,\s*(['"`])(/[^'"`\s${}?#]*)""")
_NOT_GET = re.compile(r"""method\s*:\s*['"`](?!get\b)""", re.IGNORECASE)
_STATIC_EXT = (".js", ".mjs", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".woff", ".woff2", ".map", ".html")
_SCRIPT_SRC = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


def find_endpoints(root: Path, page: Path | None = None, limit: int = MAX_ENDPOINTS) -> list[str]:
    """Same-origin GET paths the page's own code requests (`fetch('/api/x')`, axios.get, $.getJSON, XHR GET).

    Only plain GETs: a request with a method other than GET could change data, so it is never sent. Paths built at run time
    (template strings with ${...}) and static files are skipped."""
    sources: list[str] = []
    root = Path(root)
    if page and Path(page).is_file():
        html = _read(Path(page))
        sources.append(html)
        for src in _SCRIPT_SRC.findall(html):
            if src.startswith(("http:", "https:", "//", "data:")):
                continue
            local = (root / src.lstrip("/")) if src.startswith("/") else (Path(page).parent / src.split("?")[0])
            sources.append(_read(local))
    else:
        for path in list(root.rglob("*.js"))[:40]:
            if not (set(path.relative_to(root).parts) & _SKIP_DIRS):
                sources.append(_read(path))
    seen: list[str] = []
    for text in sources:
        found = []
        for match in _FETCH.finditer(text):
            options = match.group(3) or ""
            if not _NOT_GET.search(options):
                found.append(match.group(2))
        found += [m.group(2) for m in _AXIOS.finditer(text)] + [m.group(2) for m in _XHR.finditer(text)]
        for path in found:
            if path.lower().endswith(_STATIC_EXT) or path in seen or path == "/":
                continue
            seen.append(path)
    return seen[:limit]


# --- running it -------------------------------------------------------------------------------------

def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_get(url: str, timeout: float = PROBE_SECONDS) -> int:
    """The status code the server answered, or 0 when it did not answer."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "agent8088-backend-check"}), timeout=timeout) as reply:
            reply.read(65536)
            return reply.status
    except urllib.error.HTTPError as error:
        return error.code
    except Exception:  # noqa: BLE001
        return 0


def stop_process(proc) -> None:
    """End the launched wrapper and everything it started."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


_ENVIRONMENT = re.compile(
    r"EADDRINUSE|Address already in use|WinError 10048|EACCES|Permission denied|PermissionError|Read-only file system|"
    r"readonly database|attempt to write a readonly|unable to open database|spawn EPERM|\[sandbox\]|Access is denied|"
    r"command not found|is not recognized as an internal", re.IGNORECASE)
_MISSING_PY = re.compile(r"ModuleNotFoundError: No module named '([^'.]+)")
_MISSING_JS = re.compile(r"Cannot find module '([^']+)'")


def classify_exit(output: str, project: Path) -> tuple[str, str]:
    """(status, detail) for a server that exited before it answered, from the tail of what it printed."""
    tail = (output or "").strip()[-600:]
    last = next((line.strip() for line in reversed(tail.splitlines()) if line.strip()), "no output")[:160]
    if _ENVIRONMENT.search(tail):
        return UNAVAILABLE, f"the server could not start in this sandbox: {last}"
    missing = _MISSING_PY.search(tail)
    if missing and not (project / f"{missing.group(1)}.py").exists() and not (project / missing.group(1)).exists():
        return UNAVAILABLE, f"a dependency is not installed here ({missing.group(1)}): {last}"
    missing = _MISSING_JS.search(tail)
    if missing and not missing.group(1).startswith((".", "/")):
        return UNAVAILABLE, f"a dependency is not installed here ({missing.group(1)}): {last}"
    return FAILED, f"the server exited before it accepted a connection: {last}"


def _output_of(proc) -> str:
    """What the launched server printed: the launcher's log file (`proc.output_file`), else a captured stdout."""
    path = getattr(proc, "output_file", None)
    try:
        if path:
            return Path(path).read_text(encoding="utf-8", errors="replace")[-4000:]
        return (proc.stdout.read() or b"").decode("utf-8", "replace") if getattr(proc, "stdout", None) else ""
    except Exception:  # noqa: BLE001
        return ""


Launcher = Callable[[Entry, int, Path], "subprocess.Popen"]


def run_backend_check(root: Path, page: Path | None, launcher: Launcher | None, *, startup: float = STARTUP_SECONDS,
                      probe: Callable[[str], int] = http_get, in_use: Callable[[int], bool] = port_in_use,
                      pick_port: Callable[[], int] = free_port, stop: Callable = stop_process,
                      sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> BackendReport:
    root = Path(root)
    entry = find_entry(root)
    if entry is None:
        return BackendReport(NOT_APPLICABLE, "the project has no server of its own that could be found")
    name = f"{entry.kind} {entry.script.name}"
    if launcher is None:
        return BackendReport(UNAVAILABLE, "the sandbox cannot start a server here (supported on the Windows native sandbox only so far)", server=name)
    port = entry.port or pick_port()
    if in_use(port):
        return BackendReport(UNAVAILABLE, f"port {port} is already in use by another program, so the project's server was not started", server=name)
    endpoints = find_endpoints(root, page)
    try:
        proc = launcher(entry, port, root)
    except Exception as error:  # noqa: BLE001
        return BackendReport(UNAVAILABLE, f"the server could not be launched: {str(error)[:120]}", server=name)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = clock() + startup
        answered = False
        while clock() < deadline:
            if proc.poll() is not None:
                break
            if probe(base + "/") != 0:
                answered = True
                break
            sleep(0.4)
        if not answered:
            if proc.poll() is not None:
                status, detail = classify_exit(_output_of(proc), entry.cwd)
                return BackendReport(status, detail, server=name)
            return BackendReport(UNAVAILABLE, f"the server did not answer within {startup:.0f}s (it may need a build or install step)", server=name)
        results, failures = [], []
        for path in endpoints:
            code = probe(base + path)
            results.append({"path": path, "status": code})
            if code == 0 or code >= 400:
                failures.append(f"GET {path} -> {code if code else 'no answer'}")
        if failures:
            return BackendReport(FAILED, "the project's own server answered badly: " + "; ".join(failures[:4]), results, name)
        detail = (f"the server started and {len(results)} endpoint{'s' if len(results) != 1 else ''} the page uses answered"
                  if results else "the server started and answered; the page's code names no GET endpoint to probe")
        return BackendReport(PASSED, detail, results, name)
    finally:
        stop(proc)
        path = getattr(proc, "output_file", None)
        if path:
            try:
                Path(path).unlink()
            except OSError:
                pass
