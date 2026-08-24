"""_exec_browser must point Playwright at a browsers directory inside
$AGENT8088_HOME rather than the OS-default shared cache - that shared cache
can be used by other Playwright-based projects on the same machine, so
`agent8088 --uninstall` can't safely delete it. Installing/looking inside our
own subdirectory means the existing home-directory wipe already covers it.
"""
import os
import sys
import types
from contextlib import contextmanager

import pytest

from agent8088 import engine


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)


def _install_fake_playwright(monkeypatch):
    """Stub playwright.sync_api so _exec_browser stops at the
    'Chromium browser is not installed' check instead of launching a real
    browser - that's enough to exercise the PLAYWRIGHT_BROWSERS_PATH line
    without a real Playwright/Chromium install in the test environment."""

    class FakeChromium:
        executable_path = "/nonexistent/chromium-for-test"

    class FakeDriver:
        chromium = FakeChromium()

    @contextmanager
    def fake_sync_playwright():
        yield FakeDriver()

    fake_module = types.SimpleNamespace(sync_playwright=fake_sync_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)


def test_exec_browser_sets_playwright_browsers_path_inside_agent_home(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    _install_fake_playwright(monkeypatch)

    result = engine._exec_browser({"url": "https://example.com"})

    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(tmp_path / "agent8088" / "playwright-browsers")
    assert "Chromium browser is not installed" in result


def test_exec_browser_respects_existing_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/path")
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: True)
    _install_fake_playwright(monkeypatch)

    engine._exec_browser({"url": "https://example.com"})

    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == "/custom/path"


def test_exec_browser_does_not_set_env_var_when_playwright_unavailable(monkeypatch, tmp_path):
    """The env var is only useful once Playwright itself is installed - setting
    it before the availability check would just be dead state on a machine
    where the optional [browser] extra was never installed."""
    monkeypatch.setattr(engine, "_agent_data_dir", lambda: tmp_path / "agent8088")
    monkeypatch.setattr(engine, "_egress_check", lambda url: None)
    monkeypatch.setattr(engine, "_ssrf_check", lambda url: None)
    monkeypatch.setattr(engine, "_playwright_available", lambda: False)

    result = engine._exec_browser({"url": "https://example.com"})

    assert "not installed" in result
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
