"""Helpers the task graders share. Imported by path from bench/tasks/<task>/.

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import graderlib
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def installed_chromes() -> list:
    """Chromium executables in the Playwright browsers folder, newest first.

    Used when the installed Playwright expects a browser build other than the one
    downloaded; any recent Chromium renders these pages the same way."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root:
        return []
    names = ("chrome.exe", "chrome", "chrome-headless-shell.exe", "chrome-headless-shell", "Chromium")
    found = [path for path in Path(root).glob("chromium*/**/*") if path.name in names and path.is_file()]
    return sorted(found, key=str, reverse=True)


def launch_chromium(driver):
    """Start Chromium, trying the other installed builds when the expected one is missing."""
    failures = []
    for executable in [None, *installed_chromes()]:
        try:
            return driver.chromium.launch(executable_path=str(executable) if executable else None)
        except Exception as exc:  # noqa: BLE001 - every launch failure means: try the next build
            failures.append(exc)
    raise failures[0]


class OfflinePage:
    """A headless page that blocks every request leaving the machine and records console errors.

        with graderlib.OfflinePage() as session:
            page = session.open(path)          # a file on disk, as a file:// address
            ...
            session.errors, session.outside   # what the page logged and tried to fetch

    Raises when no browser can be started; graders turn that into an ungraded report."""

    def __init__(self):
        self.errors: list = []
        self.outside: list = []
        self._driver = self._browser = self._context = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._manager = sync_playwright()
        self._driver = self._manager.__enter__()
        try:
            self._browser = launch_chromium(self._driver)
        except Exception:
            self._manager.__exit__(None, None, None)
            raise
        self._context = self._browser.new_context()
        self._context.route("**/*", self._route)
        return self

    def _route(self, request_route):
        url = request_route.request.url
        if url.startswith(("file:", "data:", "about:", "blob:")):
            request_route.continue_()
        else:
            self.outside.append(url)
            request_route.abort()

    def open(self, path: Path, query: str = ""):
        page = self._context.new_page()
        page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(Path(path).resolve().as_uri() + query)
        page.wait_for_timeout(300)
        return page

    def __exit__(self, *exc):
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._manager.__exit__(None, None, None)
        return False


def browser_error(exc: Exception) -> dict:
    """The report for a run the grader could not check because no browser started."""
    return {"error": f"the grader could not start a browser: {str(exc).splitlines()[0][:200] if str(exc) else exc!r}"}


GRADER_WSL_DISTRO_ENV = "AGENT8088_GRADER_WSL_DISTRO"


def to_wsl_path(path) -> str:
    """A Windows path as the WSL distro sees it: E:\\a b\\c is /mnt/e/a b/c."""
    text = str(path).replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        return f"/mnt/{text[0].lower()}{text[2:]}"
    return text


def venv_python_command(workspace: Path, distro: str | None = None, wsl: str | None = None,
                        windows: bool | None = None) -> list | None:
    """The command that starts the venv's Python in workspace/.venv, or None when there is no venv.

    On Windows a venv the agent built inside the WSL sandbox has bin/python, a link to the distro's Python that
    Windows cannot start; it runs in that distro instead (named by AGENT8088_GRADER_WSL_DISTRO). The command then
    starts in the workspace, so run it with the workspace as its folder either way."""
    windows = (sys.platform == "win32") if windows is None else windows
    venv = Path(workspace) / ".venv"
    candidates = [venv / "Scripts" / "python.exe"] + ([] if windows else [venv / "bin" / "python"])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return [str(candidate)]
        except OSError:
            continue
    linux = venv / "bin" / "python"
    if windows and os.path.lexists(linux):
        distro = distro if distro is not None else os.environ.get(GRADER_WSL_DISTRO_ENV, "")
        wsl = wsl if wsl is not None else (shutil.which("wsl.exe") or "")
        if distro and wsl:
            where = to_wsl_path(os.path.abspath(workspace))
            return [wsl, "-d", distro, "--cd", where, "--exec", f"{where}/.venv/bin/python"]
    return None


def report(checks: dict, total: int | None = None, **extra) -> dict:
    """{"passed", "score", "checks"}; with `total`, checks that never ran count as failed."""
    total = total or len(checks) or 1
    return {"passed": len(checks) == total and all(checks.values()),
            "score": round(sum(bool(v) for v in checks.values()) / total, 3), "checks": checks, **extra}


def grade_in_browser(play, total: int) -> dict:
    """Run `play(session, checks)` in an OfflinePage and report what it found.

    No Playwright or no browser: an ungraded report (an "error", no "passed"). A step that fails part way (an
    element that is not there, a click that times out) ends the play; the checks it never reached count as failed."""
    checks: dict = {}
    try:
        session = OfflinePage().__enter__()
    except ImportError:
        return {"error": "playwright is not installed for the grader"}
    except Exception as exc:  # noqa: BLE001 - every launch failure means the run cannot be graded here
        return browser_error(exc)
    failure = ""
    try:
        play(session, checks)
    except Exception as exc:  # noqa: BLE001 - the work under test failed a step
        failure = (str(exc).splitlines() or [repr(exc)])[0][:200]
    finally:
        errors, outside = session.errors[:5], session.outside[:5]
        session.__exit__(None, None, None)
    extra = {"console_errors": errors, "outside_requests": outside}
    if failure:
        extra["failure"] = failure
    return report(checks, total=total, **extra)


def run_checker(check: Path, args: list, timeout: int = 1800) -> dict:
    """Run a task's original checker, which prints one JSON report, and return that report.

    A checker that crashes or prints no JSON gives {"error": ...}, so the run counts
    as ungraded rather than failed."""
    try:
        done = subprocess.run([sys.executable, str(check), *map(str, args)], capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace", check=False)
    except subprocess.TimeoutExpired:
        return {"error": f"checker timed out after {timeout}s"}
    text = done.stdout.strip()
    start = text.find("{")
    try:
        return json.loads(text[start:]) if start >= 0 else {"error": f"checker printed no report: {done.stderr[-300:]}"}
    except ValueError:
        return {"error": f"checker report is not JSON: {done.stderr[-300:]}"}


def write(report: dict, path: str) -> None:
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
