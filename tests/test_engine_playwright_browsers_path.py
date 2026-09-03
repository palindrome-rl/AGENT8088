"""_exec_browser prefers a browsers directory inside $AGENT8088_HOME over the
OS-default shared cache - that shared cache can be used by other
Playwright-based projects on the same machine, so `agent8088 --uninstall`
can't safely delete it. Looking inside our own subdirectory first means the
existing home-directory wipe already covers what we downloaded.

It is a preference, not a hard pin: forcing it meant a machine that already
had a valid, version-matching Chromium in the shared cache was told "Chromium
browser is not installed", leaving browse_page dead until the user either
re-downloaded 280MB or discovered the env var. The fallback lives in
test_browse_page_missing_chromium.py; what this file protects is that our own
directory still *wins* when it has a browser, and that a failed lookup leaves
no state behind that would break the next attempt.
"""
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent8088 import engine


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)


def _install_fake_playwright(monkeypatch, rel=None, default_root=None):
    """Stub playwright.sync_api without needing a real Chromium download.

    When rel/default_root are given the stub resolves its executable_path from
    PLAYWRIGHT_BROWSERS_PATH the way real Playwright does (<root>/<rel>), which
    is what lets a test tell the candidate directories apart. Left out, it
    reports a path that never exists, so _exec_browser stops at the
    'Chromium browser is not installed' check.
    """
    @contextmanager
    def fake_sync_playwright():
        if rel is None:
            exe = "/nonexistent/chromium-for-test"
        else:
            root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(default_root)
            exe = str(Path(root) / rel)

        class FakeChromium:
            executable_path = exe

        class FakeDriver:
            chromium = FakeChromium()

        yield FakeDriver()

    fake_module = types.SimpleNamespace(sync_playwright=fake_sync_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)


def test_our_own_browsers_dir_wins_when_it_holds_the_browser(monkeypatch, tmp_path):
    """The uninstall-safety property: a browser we own is the one we use."""
    home = tmp_path / "agent8088"
    private = home / "playwright-browsers"
    exe = private / "chromium-1234" / "chrome"
    exe.parent.mkdir(parents=True)
    exe.write_text("stub")

    monkeypatch.setattr(engine, "_agent_data_dir", lambda: home)
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    _install_fake_playwright(monkeypatch, rel="chromium-1234/chrome",
                             default_root=tmp_path / "shared")

    assert engine._playwright_chromium_executable() == str(exe)
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(private)


def test_a_failed_lookup_leaves_no_env_state_behind(monkeypatch, tmp_path):
    """Otherwise the next attempt reads our own leftover value as an explicit
    operator choice and never re-checks the other location - so installing
    Chromium and retrying in the same session would keep failing."""
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    _install_fake_playwright(monkeypatch, rel="chromium-1234/chrome",
                             default_root=tmp_path / "shared")

    assert engine._playwright_chromium_executable() is None
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ


def test_exec_browser_respects_existing_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/path")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    _install_fake_playwright(monkeypatch)

    engine._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == "/custom/path"


def test_exec_browser_does_not_set_env_var_when_playwright_unavailable(monkeypatch, tmp_path):
    """The env var is only useful once Playwright itself is installed - setting
    it before the availability check would just be dead state on a machine
    where the optional [browser] extra was never installed."""
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)

    result = engine._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert "not installed" in result
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
