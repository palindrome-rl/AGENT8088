"""Backend check: does the project's own server start, and do the endpoints its page uses answer?

The page check (app_check) serves static files only, so a page that loads its data from its own server (`fetch('/api/pokemon')`)
passes without that server ever running, and the verdict says only "the HTML loaded". This check is optional (off by default,
`backend_check=1` in config.txt) and fills ONE separate field, "backend check". It never changes the verification level.

    off              the setting is off (nothing is recorded)
    not applicable   the project has no server of its own that could be found
    unavailable      it could not be checked here: port in use, a dependency or build step the sandbox lacks, a write the
                     read-only sandbox refuses, no sandbox launcher on this platform, the server never answered in time
    passed           the server started, and discovered literal GET endpoints answered 2xx or 3xx (status only)
    failed           the project's own code failed: the server died on its own error, stopped answering, or an endpoint the
                     page uses returned 4xx or 5xx

Only evidence that comes from the project's own code is a "failed"; everything about the machine is "unavailable". The server is
started through a launcher the engine supplies (the sandbox wrapper, kept alive by this module while the endpoints are probed and
stopped afterwards), so nothing here runs a generated server outside the sandbox. A port that is already in use is never
tested: that would verify somebody else's server.
"""
from __future__ import annotations

import json
import http.client
import ipaddress
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
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


def _safe_file(path: Path, boundary: Path, deny=None) -> Path | None:
    """Resolve before reading: neither traversal nor a symlink can widen the project."""
    try:
        real = path.resolve()
        if not real.is_relative_to(boundary.resolve()) or (deny and deny(str(real))) or not real.is_file():
            return None
        return real
    except (OSError, ValueError, RuntimeError):
        return None


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        # A truncated program can hide a method or the rest of a call. Fail closed.
        return raw.decode('utf-8', 'replace') if len(raw) <= limit else ''
    except OSError:
        return ""


def find_entry(root: Path, *, boundary: Path | None = None, deny=None) -> Entry | None:
    """The project's own server: a `npm start` script, else a conventional server file that really starts one.

    Looked for in the served folder and up to three folders above it (the page may sit in public/)."""
    folder = Path(root).resolve()
    boundary = Path(boundary or root).resolve()
    if not folder.is_relative_to(boundary):
        return None
    for _ in range(4):
        found = _entry_in(folder, boundary=boundary, deny=deny)
        if found:
            return found
        if folder == boundary or folder.parent == folder:
            break
        folder = folder.parent
    return None


def _entry_in(folder: Path, *, boundary: Path | None = None, deny=None) -> Entry | None:
    folder = Path(folder).resolve()
    boundary = Path(boundary or folder).resolve()
    package = _safe_file(folder / 'package.json', boundary, deny)
    if package:
        try:
            start = (json.loads(_read(package)).get("scripts") or {}).get("start", "")
        except (ValueError, AttributeError):
            start = ""
        # Only a direct Node entry, never npm hooks or shell fragments.
        match = re.fullmatch(r'''\s*node\s+(['"]?)([\w ./\\-]+\.(?:m?js|cjs))\1\s*''', str(start))
        script = _safe_file(folder / match.group(2), boundary, deny) if match else None
        if script:
            return _make_entry('node', script, folder)
    for name in ("server.js", "server.mjs", "start-server.js", "index.js", "app.js"):
        path = _safe_file(folder / name, boundary, deny)
        if path and _JS_SERVER.search(_read(path)):
            return _make_entry("node", path, folder)
    for name in ("server.py", "app.py", "main.py", "run.py"):
        path = _safe_file(folder / name, boundary, deny)
        if path and _PY_SERVER.search(_read(path)):
            return _make_entry("python", path, folder)
    return None


def _make_entry(kind: str, script: Path, cwd: Path) -> Entry:
    text = _read(script)
    match = _LITERAL_PORT.search(text)
    uses_env = bool(re.search(r"process\.env\.PORT|environ(?:\.get)?\s*[\[(]\s*['\"]PORT", text, re.IGNORECASE))
    port = int(match.group(1)) if match and not uses_env else None
    return Entry(kind, script, cwd, port if port and 0 < port < 65536 else None,
                 port_arg=(kind == "python" and "sys.argv" in text and not match and not uses_env))


# --- the endpoints the page uses --------------------------------------------------------------------

# Accept complete literal argument lists only. Unknown options, spreads, computed
# paths and arbitrary request objects cannot be assumed to mean GET.
_PATH_LITERAL = r'''(['"`])(/[^'"`\s${}\\#]*)\1'''
_FETCH = re.compile(r'\bfetch\s*\(\s*' + _PATH_LITERAL + r'''\s*(?:,\s*\{\s*(?:method\s*:\s*(['"])GET\3\s*,?\s*)?\})?\s*\)''', re.I)
_AXIOS = re.compile(r'''(?:\baxios|\$|\bjQuery)\.(?:get|getJSON)\s*\(\s*''' + _PATH_LITERAL + r'\s*\)')
_XHR = re.compile(r'''\.open\s*\(\s*['"]GET['"]\s*,\s*(['"`])(/[^'"`\s${}\\#]*)\1\s*(?:,\s*true\s*)?\)''', re.I)
_STATIC_EXT = (".js", ".mjs", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".woff", ".woff2", ".map", ".html")
class _PageScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline, self.external, self.current = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            src = dict(attrs).get('src')
            if src and len(self.external) < 40:
                self.external.append(src)
            self.current = [] if not src else None

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.inline.append(''.join(self.current))
            self.current = None


def _literal_requests(text: str):
    """Find complete supported calls outside comments and string/regex literals.

    This is intentionally not a JavaScript interpreter. Unknown syntax is
    skipped, never executed or assumed to be a safe request.
    """
    i = 0
    while i < len(text):
        if text.startswith('//', i):
            end = text.find('\n', i + 2)
            i = end if end >= 0 else len(text)
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = end + 2 if end >= 0 else len(text)
        elif text[i] in "'\"`/":
            quote = text[i]
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
        else:
            match = next((m for pattern in (_FETCH, _AXIOS, _XHR) if (m := pattern.match(text, i))), None)
            if match:
                yield match.group(2)
                i = match.end()
            else:
                i += 1


def find_endpoints(root: Path, page: Path | None = None, limit: int = MAX_ENDPOINTS, *, deny=None) -> list[str]:
    """Same-origin GET paths the page's own code requests (`fetch('/api/x')`, axios.get, $.getJSON, XHR GET).

    Only plain GETs: a request with a method other than GET could change data, so it is never sent. Paths built at run time
    (template strings with ${...}) and static files are skipped."""
    sources: list[str] = []
    root = Path(root).resolve()
    supplied_page = page
    page = _safe_file(Path(page), root, deny) if page else None
    if supplied_page and page is None:
        return []
    if page:
        html = _read(page)
        scripts = _PageScripts()
        scripts.feed(html)
        sources.extend(scripts.inline)
        for src in scripts.external:
            parsed = urllib.parse.urlsplit(src)
            if parsed.scheme or parsed.netloc:
                continue
            src_path = parsed.path
            local = (root / src_path.lstrip('/')) if src_path.startswith('/') else (page.parent / src_path)
            local = _safe_file(local, root, deny)
            if local:
                sources.append(_read(local))
    else:
        count = 0
        for visited, (folder, dirs, names) in enumerate(os.walk(root, followlinks=False)):
            if visited >= 160:
                break
            dirs[:] = [name for name in dirs if name not in _SKIP_DIRS and not (Path(folder) / name).is_symlink()]
            for name in names:
                if not name.endswith(('.js', '.mjs', '.cjs')):
                    continue
                local = _safe_file(Path(folder) / name, root, deny)
                if local:
                    sources.append(_read(local))
                    count += 1
                if count >= 40:
                    break
            if count >= 40:
                break
    seen: list[str] = []
    for text in sources:
        for path in _literal_requests(text):
            if (not _safe_endpoint(path) or urllib.parse.urlsplit(path).path.lower().endswith(_STATIC_EXT)
                    or path in seen or path == '/'):
                continue
            seen.append(path)
    return seen[:max(0, min(limit, MAX_ENDPOINTS))]


def _safe_endpoint(path: str) -> bool:
    decoded = urllib.parse.unquote(path)
    return (path.startswith('/') and not decoded.startswith('//') and '\\' not in decoded
            and not any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            and not any(part in {'.', '..'} for part in decoded.split('?', 1)[0].split('/')))


# --- running it -------------------------------------------------------------------------------------

def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def owns_port(proc, port: int) -> bool:
    """A busy-port precheck is not ownership: attribute the listener before every probe."""
    try:
        import psutil
        parent = psutil.Process(proc.pid)
        pids = {parent.pid} | {child.pid for child in parent.children(recursive=True)}
        return any(conn.status == psutil.CONN_LISTEN and conn.pid in pids and conn.laddr
                   and conn.laddr.port == port and conn.laddr.ip in {'127.0.0.1', '0.0.0.0', '::'}
                   for conn in psutil.net_connections(kind='tcp'))
    except Exception:  # noqa: BLE001 -- inability to attribute a port is not permission to probe another service
        return False


class ProbeUnavailable(RuntimeError):
    """The connected peer could not be attributed; no HTTP request was sent."""


def owns_connection(proc, sock, port: int) -> bool:
    """Check the established peer too, closing the listener-check/rebind race.

    Node commonly listens on a dual-stack IPv6 socket; compare IPv4-mapped
    loopback addresses as well as IPv4 without accepting an IPv6-only listener
    as proof that an unrelated IPv4 service belongs to this process tree.
    """
    try:
        import psutil
        parent = psutil.Process(proc.pid)
        pids = {parent.pid} | {child.pid for child in parent.children(recursive=True)}
        client_port = sock.getsockname()[1]
        for conn in psutil.net_connections(kind='tcp'):
            if (conn.pid in pids and conn.laddr and conn.raddr and conn.laddr.port == port
                    and conn.raddr.port == client_port and conn.status == psutil.CONN_ESTABLISHED):
                local, remote = ipaddress.ip_address(conn.laddr.ip), ipaddress.ip_address(conn.raddr.ip)
                local = getattr(local, 'ipv4_mapped', None) or local
                remote = getattr(remote, 'ipv4_mapped', None) or remote
                if local.is_loopback and remote.is_loopback:
                    return True
        return False
    except Exception:  # noqa: BLE001
        return False


def http_get(url: str, timeout: float = PROBE_SECONDS, validate=None) -> int:
    """One literal loopback GET, without environment proxies, redirects or body reads."""
    connection = None
    timer = None
    try:
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port
                or parts.username or parts.password or not _safe_endpoint(parts.path or '/')):
            return 0
        # http.client neither follows redirects nor consults proxy variables.
        connection = http.client.HTTPConnection('127.0.0.1', parts.port, timeout=max(0.001, timeout))
        started = time.monotonic()
        connection.connect()
        sock = connection.sock

        def expire():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

        # A socket's timeout alone restarts on each byte; a slow-drip response
        # must not keep this optional check alive indefinitely.
        timer = threading.Timer(max(0.001, timeout - (time.monotonic() - started)), expire)
        timer.daemon = True
        timer.start()
        if validate is not None and not validate(sock):
            raise ProbeUnavailable('the connected peer could not be attributed to the launched server; no HTTP request was sent')
        target = urllib.parse.quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
        if parts.query:
            target += '?' + urllib.parse.quote(parts.query, safe="=&?/:@!$'()*+,;%-._~")
        connection.request('GET', target, headers={'User-Agent': 'agent8088-backend-check'})
        return connection.getresponse().status
    except ProbeUnavailable:
        raise
    except Exception:  # noqa: BLE001
        return 0
    finally:
        if timer:
            timer.cancel()
        if connection:
            connection.close()


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
    r"command not found|is not recognized as an internal|windows-acl-run", re.IGNORECASE)
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
        reader = getattr(proc, 'backend_reader', None)
        if reader:
            reader.join(timeout=1)
            return proc.backend_output.decode('utf-8', 'replace')
        if path:
            with Path(path).open('rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 4000))
                return stream.read(4000).decode('utf-8', 'replace')
        return (proc.stdout.read() or b"").decode("utf-8", "replace") if getattr(proc, "stdout", None) else ""
    except Exception:  # noqa: BLE001
        return ""


def capture_output(proc, limit: int = 4000) -> None:
    """Drain a real launcher's pipe continuously without an unbounded host log."""
    proc.backend_output = b''

    def read():
        try:
            while chunk := proc.stdout.read(1024):
                proc.backend_output = (proc.backend_output + chunk)[-limit:]
        except (OSError, ValueError):
            pass
        finally:
            proc.stdout.close()

    proc.backend_reader = threading.Thread(target=read, daemon=True)
    proc.backend_reader.start()


Launcher = Callable[[Entry, int, Path], "subprocess.Popen"]


def run_backend_check(root: Path, page: Path | None, launcher: Launcher | None, *, startup: float = STARTUP_SECONDS,
                      probe: Callable[[str], int] = http_get, in_use: Callable[[int], bool] = port_in_use,
                      pick_port: Callable[[], int] = free_port, stop: Callable = stop_process,
                      sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                      boundary: Path | None = None, deny=None, owned: Callable = owns_port,
                      cancelled: Callable[[], bool] | None = None) -> BackendReport:
    root = Path(root).resolve()
    entry = find_entry(root, boundary=boundary, deny=deny)
    if entry is None:
        return BackendReport(NOT_APPLICABLE, "the project has no server of its own that could be found")
    name = f"{entry.kind} {entry.script.name}"
    if launcher is None:
        return BackendReport(UNAVAILABLE, "the sandbox cannot start a server here (supported on the Windows native sandbox only so far)", server=name)
    if cancelled and cancelled():
        return BackendReport(UNAVAILABLE, 'the request budget was exhausted before the backend check', server=name)
    port = entry.port or pick_port()
    if in_use(port):
        return BackendReport(UNAVAILABLE, f"port {port} is already in use by another program, so the project's server was not started", server=name)
    endpoints = find_endpoints(entry.cwd, page, deny=deny)
    try:
        proc = launcher(entry, port, Path(boundary or root).resolve())
    except Exception as error:  # noqa: BLE001
        return BackendReport(UNAVAILABLE, f"the server could not be launched: {str(error)[:120]}", server=name)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = clock() + startup
        answered = False
        def request(path, remaining=PROBE_SECONDS):
            url = base + path
            return (http_get(url, timeout=min(PROBE_SECONDS, remaining), validate=lambda sock: owns_connection(proc, sock, port))
                    if probe is http_get else probe(url))

        while clock() < deadline:
            if cancelled and cancelled():
                return BackendReport(UNAVAILABLE, 'the request budget was exhausted during the backend check', server=name)
            if proc.poll() is not None:
                break
            if owned(proc, port) and request('/', max(0.001, deadline - clock())) != 0:
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
            if cancelled and cancelled():
                return BackendReport(UNAVAILABLE, 'the request budget was exhausted during the backend check', results, name)
            if not owned(proc, port):
                return BackendReport(UNAVAILABLE, 'the listener no longer belongs to the launched server; no further requests were sent', results, name)
            code = request(path)
            results.append({"path": path, "status": code})
            if code == 0 or code >= 400:
                failures.append(f"GET {path} -> {code if code else 'no answer'}")
        if failures:
            return BackendReport(FAILED, "the project's own server answered badly: " + "; ".join(failures[:4]), results, name)
        if proc.poll() is not None:
            status, detail = classify_exit(_output_of(proc), entry.cwd)
            return BackendReport(status, detail, results, name)
        detail = (f"the server started and {len(results)} discovered GET endpoint{'s' if len(results) != 1 else ''} answered (status only; redirects and page integration were not followed)"
                  if results else "the server started and answered; the page's code names no GET endpoint to probe")
        return BackendReport(PASSED, detail, results, name)
    except ProbeUnavailable as error:
        return BackendReport(UNAVAILABLE, str(error), server=name)
    finally:
        stop(proc)
        reader = getattr(proc, 'backend_reader', None)
        if reader:
            reader.join(timeout=1)
        path = getattr(proc, "output_file", None)
        if path:
            try:
                Path(path).unlink()
            except OSError:
                pass
