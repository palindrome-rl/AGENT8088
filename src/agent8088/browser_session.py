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
from typing import Any, Callable, Tuple

from browser_use import BrowserProfile

_log = logging.getLogger(__name__)

_SESSION_DIR: Path | None = None
_ACTIVE_PROFILE: BrowserProfile | None = None
_SESSION_PROXY_URL: str | None = None
_SESSION_PROXY_STOP: Callable[[], None] | None = None


def get_session_user_data_dir() -> Path:
    """Return the temp user-data-dir for the current process session, creating it if needed."""
    global _SESSION_DIR
    if _SESSION_DIR is None or not _SESSION_DIR.exists():
        temp_base = Path(tempfile.gettempdir()).resolve()
        _SESSION_DIR = (temp_base / f"agent8088-browser-session-{os.getpid()}").resolve()
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


def is_browser_session_active() -> bool:
    """Return True if an in-session browser profile or directory is currently allocated."""
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
    user_data_dir = str(get_session_user_data_dir())
    kwargs = dict(base_kwargs or {})
    kwargs["user_data_dir"] = user_data_dir
    kwargs["keep_alive"] = True
    if executable_path:
        kwargs["executable_path"] = executable_path

    _ACTIVE_PROFILE = BrowserProfile(**kwargs)
    return _ACTIVE_PROFILE


def cleanup_browser_session() -> None:
    """Clean up the active browser session, proxy, and remove the session temp directory."""
    global _SESSION_DIR, _ACTIVE_PROFILE, _SESSION_PROXY_URL, _SESSION_PROXY_STOP
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


# Register process-exit cleanup
atexit.register(cleanup_browser_session)
