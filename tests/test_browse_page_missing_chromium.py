"""_exec_browser() must not let a missing Chromium *binary* surface as a raw
playwright exception.

`playwright` the Python package is a core dependency (always installed by
pyproject.toml), but `playwright install chromium` is a separate ~280 MB
download the installer runs afterward and can fail or be skipped
independently (network blip, disk space, antivirus interference - see the
Windows/Linux installer hardening work in this same area). Before this fix,
_exec_browser only checked that the package imported, so a missing browser
binary fell straight into playwright's own multi-paragraph "Executable
doesn't exist" error - which reads as a crash, not an install step, to
whoever just pasted a link expecting browse_page to work.
"""
import os
import sys
import types
from pathlib import Path

import pytest

from agent8088 import engine as A


class _FakeChromium:
    def __init__(self, executable_path, launch_calls):
        self.executable_path = executable_path
        self._launch_calls = launch_calls

    def launch(self, **kwargs):
        self._launch_calls.append(kwargs)
        raise AssertionError("launch() must not be called when Chromium is missing")


class _FakePlaywrightSession:
    def __init__(self, executable_path, launch_calls):
        self.chromium = _FakeChromium(executable_path, launch_calls)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_sync_playwright(monkeypatch, executable_path, launch_calls):
    fake_module = types.SimpleNamespace(
        sync_playwright=lambda: _FakePlaywrightSession(executable_path, launch_calls))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    monkeypatch.setattr(A, "_playwright_available", lambda: True)


def test_missing_chromium_binary_returns_clean_install_instructions(monkeypatch, tmp_path):
    launch_calls = []
    missing_path = str(tmp_path / "chromium" / "chrome.exe")
    _install_fake_sync_playwright(monkeypatch, missing_path, launch_calls)

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert "Chromium browser is not installed" in result
    assert "playwright install chromium" in result
    assert launch_calls == []


def test_present_chromium_binary_proceeds_to_run_the_browser_agent(monkeypatch, tmp_path):
    present_path = tmp_path / "chrome.exe"
    present_path.write_text("stub")
    _install_fake_sync_playwright(monkeypatch, str(present_path), launch_calls=[])

    calls = []

    async def fake_run_browser_agent(url, task, executable_path=None):
        calls.append((url, task))
        return "ok"

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    result = A._exec_browser({"url": "https://example.com", "task": "extract the heading"})

    # It gets past the Chromium-presence check and reaches the browser-use
    # agent runner - proving the check does not block a genuinely-installed
    # Chromium.
    assert calls == [("https://example.com", "extract the heading")]
    assert "ok" in result


# --- which browsers directory Chromium is looked for in ----------------------
# _exec_browser used to *force* PLAYWRIGHT_BROWSERS_PATH to agent8088's own
# directory. The intent was sound - `agent8088 --uninstall` can only delete a
# download it owns, and the OS-shared ms-playwright cache may belong to other
# Playwright projects on the same machine. But forcing it meant a machine that
# already had a valid, version-matching Chromium in the shared cache was told
# "Chromium browser is not installed", so browse_page was dead until the user
# either re-downloaded 280MB or knew to set the env var by hand.
#
# Own directory first (so uninstall stays honest), shared cache as a fallback.

_REL = Path("chromium-1234") / "chrome"


def _install_env_aware_playwright(monkeypatch, default_root):
    """sync_playwright() whose executable_path tracks PLAYWRIGHT_BROWSERS_PATH.

    Mirrors real Playwright: the browser lives at <root>/<rel>, and <root> is
    the env var when set, else Playwright's own default.
    """
    def make_session():
        root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(default_root)
        return _FakePlaywrightSession(str(Path(root) / _REL), [])

    fake_module = types.SimpleNamespace(sync_playwright=make_session)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    monkeypatch.setattr(A, "_playwright_available", lambda: True)


@pytest.fixture
def browser_dirs(monkeypatch, tmp_path):
    """A private agent8088 dir and a separate shared cache, neither populated."""
    private = tmp_path / "agent8088home" / "playwright-browsers"
    shared = tmp_path / "ms-playwright"
    monkeypatch.setattr(A, "_agent_data_dir", lambda: tmp_path / "agent8088home")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    _install_env_aware_playwright(monkeypatch, shared)
    return types.SimpleNamespace(private=private, shared=shared)


def _populate(root):
    exe = Path(root) / _REL
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("stub")
    return exe


def _reached_agent(monkeypatch):
    seen = []

    async def fake_run_browser_agent(url, task, executable_path=None):
        seen.append(executable_path)
        return "ok"

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    return seen


def test_its_own_browsers_dir_is_preferred_when_that_has_chromium(
        browser_dirs, monkeypatch):
    _populate(browser_dirs.private)
    _populate(browser_dirs.shared)
    seen = _reached_agent(monkeypatch)

    A._exec_browser({"url": "https://example.com", "task": "read it"})

    assert seen and str(browser_dirs.private) in seen[0]


def test_it_falls_back_to_the_shared_cache_when_its_own_dir_is_empty(
        browser_dirs, monkeypatch):
    _populate(browser_dirs.shared)          # only the shared cache has it
    seen = _reached_agent(monkeypatch)

    result = A._exec_browser({"url": "https://example.com", "task": "read it"})

    assert "not installed" not in result
    assert seen and str(browser_dirs.shared) in seen[0]


def test_an_explicit_browsers_path_from_the_environment_wins(
        browser_dirs, monkeypatch, tmp_path):
    chosen = tmp_path / "operator-choice"
    _populate(chosen)
    _populate(browser_dirs.private)         # would otherwise be preferred
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(chosen))
    seen = _reached_agent(monkeypatch)

    A._exec_browser({"url": "https://example.com", "task": "read it"})

    assert seen and str(chosen) in seen[0]


def test_when_no_candidate_has_chromium_it_still_reports_the_install_step(
        browser_dirs, monkeypatch):
    launched = _reached_agent(monkeypatch)

    result = A._exec_browser({"url": "https://example.com", "task": "read it"})

    assert "Chromium browser is not installed" in result
    assert "playwright install chromium" in result
    assert launched == []


def test_a_bare_playwright_install_is_now_a_valid_cure(browser_dirs, monkeypatch):
    """The hint says plain `playwright install chromium`, which writes to the
    shared cache - so accepting that location is what makes the hint true."""
    result = A._exec_browser({"url": "https://example.com", "task": "read it"})
    assert "playwright install chromium" in result

    _populate(browser_dirs.shared)          # exactly what that command does
    seen = _reached_agent(monkeypatch)

    assert "not installed" not in A._exec_browser(
        {"url": "https://example.com", "task": "read it"})
    assert seen
