"""_exec_browser drives an interactive browser-use Agent instead of a single
page.goto()+read. These tests stub _run_browser_agent (the async helper that
actually talks to browser-use) so they exercise _exec_browser's own argument
validation, pre-flight checks, role/budget bookkeeping, and output wrapping
without needing a real browser or model."""
import sys
import types

import pytest

from agent8088 import engine as A


class _FakeChromium:
    def __init__(self, executable_path):
        self.executable_path = executable_path


class _FakePlaywrightSession:
    def __init__(self, executable_path):
        self.chromium = _FakeChromium(executable_path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_present_chromium(monkeypatch, tmp_path):
    present_path = tmp_path / "chrome.exe"
    present_path.write_text("stub")
    fake_module = types.SimpleNamespace(
        sync_playwright=lambda: _FakePlaywrightSession(str(present_path)))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    monkeypatch.setattr(A, "_playwright_available", lambda: True)
    monkeypatch.setattr(A, "_egress_check", lambda url: None)
    monkeypatch.setattr(A, "_ssrf_check", lambda url: None)


def test_missing_task_is_a_clean_error(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)

    result = A._exec_browser({"url": "https://example.com"})

    assert result == "Error: browser tool requires 'task'."


def test_runs_the_browser_agent_and_wraps_the_result(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)
    calls = []

    async def fake_run_browser_agent(url, task):
        calls.append((url, task))
        return "The heading says Hello."

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert calls == [("https://example.com", "read the heading")]
    assert "The heading says Hello." in result
    assert "<<<EXTERNAL_UNTRUSTED_CONTENT" in result


def test_sets_and_restores_active_role_around_the_run(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)
    seen_role = {}

    async def fake_run_browser_agent(url, task):
        seen_role["during"] = A._active_role
        return "ok"

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert seen_role["during"] == "subagent:browser"
    assert A._active_role == "main"


def test_active_role_restored_even_when_the_run_raises(monkeypatch, tmp_path):
    _install_present_chromium(monkeypatch, tmp_path)

    async def fake_run_browser_agent(url, task):
        raise RuntimeError("boom")

    monkeypatch.setattr(A, "_run_browser_agent", fake_run_browser_agent)
    monkeypatch.setattr(A, "_active_role", "main")

    result = A._exec_browser({"url": "https://example.com", "task": "read the heading"})

    assert "Browser error" in result
    assert A._active_role == "main"
