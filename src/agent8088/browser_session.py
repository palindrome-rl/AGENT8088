"""Process-scoped browser session management for Agent8088.

Maintains a reusable browser profile and process across consecutive browse_page calls
within the active CLI session, ensuring cookies and DOM state persist while work
is in progress without saving credentials permanently to disk.
"""
from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path
import shutil
import tempfile
import asyncio
import threading
from typing import Any, Callable, Tuple

from browser_use import BrowserProfile, BrowserSession

_log = logging.getLogger(__name__)

_SESSION_DIR: Path | None = None
_ACTIVE_PROFILE: BrowserProfile | None = None
_ACTIVE_SESSION: BrowserSession | None = None
_SESSION_STARTED: bool = False
_SESSION_PROXY_URL: str | None = None
_SESSION_PROXY_STOP: Callable[[], None] | None = None
_SESSION_LOOP: asyncio.AbstractEventLoop | None = None
_SESSION_THREAD: threading.Thread | None = None


def get_session_event_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent asyncio event loop for browser tasks in this CLI session."""
    global _SESSION_LOOP, _SESSION_THREAD
    if _SESSION_LOOP is None or not _SESSION_LOOP.is_running():
        _SESSION_LOOP = asyncio.new_event_loop()
        _SESSION_THREAD = threading.Thread(
            target=_SESSION_LOOP.run_forever,
            daemon=True,
            name="agent8088-browser-session-loop",
        )
        _SESSION_THREAD.start()
    return _SESSION_LOOP


def run_in_session_loop(coro, timeout: float | None = None) -> Any:
    """Execute a coroutine on the session event loop and block for the result."""
    async def run():
        # These exceptions escape asyncio Tasks and stop the loop; carry them
        # back to the waiting caller instead, keeping the session reusable.
        try:
            return True, await coro
        except (KeyboardInterrupt, SystemExit) as exc:
            return False, exc

    loop = get_session_event_loop()
    future = asyncio.run_coroutine_threadsafe(run(), loop)
    try:
        ok, result = future.result(timeout=timeout)
    except BaseException:
        future.cancel()  # a timed-out/interrupted caller must not leave actions running
        raise
    if not ok:
        raise result
    return result


def get_session_user_data_dir() -> Path:
    """Return the temp user-data-dir for the current process session, creating it if needed."""
    global _SESSION_DIR
    if _SESSION_DIR is None or not _SESSION_DIR.exists():
        temp_base = Path(tempfile.gettempdir()).resolve()
        _SESSION_DIR = (temp_base / f"agent8088-browser-session-{os.getpid()}").resolve()
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


def is_browser_session_active() -> bool:
    """Return True if an in-session browser is currently active."""
    global _ACTIVE_SESSION, _SESSION_DIR
    if _ACTIVE_SESSION is not None and getattr(_ACTIVE_SESSION, "is_cdp_connected", False):
        return True
    return _SESSION_DIR is not None and _SESSION_DIR.exists()


def get_or_create_session_proxy(
    proxy_factory: Callable[[], Tuple[str, Callable[[], None]]],
) -> Tuple[str, Callable[[], None]]:
    """Get or start the session-lived SSRF filtering proxy."""
    global _SESSION_PROXY_URL, _SESSION_PROXY_STOP
    if _SESSION_PROXY_URL is None or _SESSION_PROXY_STOP is None:
        _SESSION_PROXY_URL, _SESSION_PROXY_STOP = proxy_factory()
    return _SESSION_PROXY_URL, _SESSION_PROXY_STOP


def build_session_browser_profile(
    proxy_url: str | None = None,
    base_kwargs: dict[str, Any] | None = None,
    executable_path: str | None = None,
) -> BrowserProfile:
    """Create or return a reusable BrowserProfile scoped to the current process session."""
    global _ACTIVE_PROFILE
    if _ACTIVE_PROFILE is not None:
        return _ACTIVE_PROFILE

    user_data_dir = str(get_session_user_data_dir())
    kwargs = dict(base_kwargs or {})
    kwargs["user_data_dir"] = user_data_dir
    kwargs["keep_alive"] = True
    if executable_path:
        kwargs["executable_path"] = executable_path

    _ACTIVE_PROFILE = BrowserProfile(**kwargs)
    return _ACTIVE_PROFILE


def get_or_create_session_browser(profile: BrowserProfile) -> BrowserSession:
    """Return the reusable BrowserSession for this process session."""
    global _ACTIVE_SESSION, _SESSION_STARTED
    if _ACTIVE_SESSION is not None and _SESSION_STARTED and not getattr(_ACTIVE_SESSION, "is_cdp_connected", False):
        _log.info("Previous active browser session disconnected; recreating session.")
        _ACTIVE_SESSION = None
        _SESSION_STARTED = False

    if _ACTIVE_SESSION is None:
        _ACTIVE_SESSION = BrowserSession(browser_profile=profile)
        _SESSION_STARTED = False
    return _ACTIVE_SESSION


def mark_session_started() -> None:
    """Mark the active browser session as started."""
    global _SESSION_STARTED
    _SESSION_STARTED = True


def cleanup_browser_session() -> None:
    """Clean up the active browser session, proxy, and remove the session temp directory."""
    global _SESSION_DIR, _ACTIVE_PROFILE, _ACTIVE_SESSION, _SESSION_STARTED
    global _SESSION_PROXY_URL, _SESSION_PROXY_STOP, _SESSION_LOOP, _SESSION_THREAD

    if _ACTIVE_SESSION is not None:
        if _SESSION_LOOP is not None and _SESSION_LOOP.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(_ACTIVE_SESSION.kill(), _SESSION_LOOP)
                future.result(timeout=5)
            except Exception as e:
                _log.debug("Error stopping browser session: %s", e)
        _ACTIVE_SESSION = None
        _SESSION_STARTED = False

    if _ACTIVE_PROFILE is not None:
        _ACTIVE_PROFILE = None

    if _SESSION_PROXY_STOP is not None:
        try:
            _SESSION_PROXY_STOP()
        except Exception as e:
            _log.debug("Error stopping session proxy: %s", e)
        _SESSION_PROXY_STOP = None
        _SESSION_PROXY_URL = None

    if _SESSION_DIR is not None and _SESSION_DIR.exists():
        try:
            shutil.rmtree(_SESSION_DIR, ignore_errors=True)
            _log.debug("Cleaned up session browser directory: %s", _SESSION_DIR)
        except Exception as e:
            _log.debug("Error cleaning up session browser directory: %s", e)
        _SESSION_DIR = None

    if _SESSION_LOOP is not None and _SESSION_LOOP.is_running():
        try:
            _SESSION_LOOP.call_soon_threadsafe(_SESSION_LOOP.stop)
            if _SESSION_THREAD is not None and _SESSION_THREAD.is_alive():
                _SESSION_THREAD.join(timeout=2)
        except Exception as e:
            _log.debug("Error stopping session loop: %s", e)
        _SESSION_LOOP = None
        _SESSION_THREAD = None


def bring_browser_to_front() -> None:
    """Bring the active browser window to the foreground on the user's desktop."""
    global _ACTIVE_SESSION, _SESSION_LOOP

    # 1. Ask CDP to bring active page to front
    if _ACTIVE_SESSION is not None and _SESSION_LOOP is not None and _SESSION_LOOP.is_running():
        async def _cdp_front():
            try:
                session = await _ACTIVE_SESSION.get_or_create_cdp_session(focus=True)
                if session and session.cdp_client:
                    await session.cdp_client.send.Page.bringToFront(session_id=session.session_id)
            except Exception:
                pass
        try:
            asyncio.run_coroutine_threadsafe(_cdp_front(), _SESSION_LOOP)
        except Exception:
            pass

    # 2. On Windows, use Win32 API to find and restore/focus the Chrome window
    import sys
    if sys.platform == "win32":
        try:
            import win32gui
            import win32con
            import win32process
            import psutil

            pids = set()
            watchdog = getattr(_ACTIVE_SESSION, "_local_browser_watchdog", None) if _ACTIVE_SESSION else None
            subproc = getattr(watchdog, "_subprocess", None) if watchdog else None
            root_pid = getattr(subproc, "pid", None)
            if root_pid:
                pids.add(root_pid)
                try:
                    p = psutil.Process(root_pid)
                    for child in p.children(recursive=True):
                        pids.add(child.pid)
                except Exception:
                    pass

            target_hwnds = []
            def _enum_win(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                        cls = win32gui.GetClassName(hwnd)
                        if (pids and win_pid in pids) or ("Chrome_WidgetWin_1" in cls and not pids):
                            target_hwnds.append(hwnd)
                    except Exception:
                        pass
                return True

            win32gui.EnumWindows(_enum_win, None)
            for hwnd in target_hwnds:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.BringWindowToTop(hwnd)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
        except Exception as e:
            _log.debug("bring_browser_to_front win32 error: %s", e)


# Register process-exit cleanup
atexit.register(cleanup_browser_session)

