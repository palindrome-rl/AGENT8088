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
import sys
import types

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

    result = A._exec_browser({"url": "https://example.com"})

    assert "Chromium browser is not installed" in result
    assert "playwright install chromium" in result
    assert launch_calls == []


def test_present_chromium_binary_proceeds_to_launch(monkeypatch, tmp_path):
    launch_calls = []
    present_path = tmp_path / "chrome.exe"
    present_path.write_text("stub")
    _install_fake_sync_playwright(monkeypatch, str(present_path), launch_calls)

    result = A._exec_browser({"url": "https://example.com"})

    # It gets past the Chromium-presence check and attempts to launch (which
    # our fake deliberately raises on) - proving the check does not block a
    # genuinely-installed Chromium.
    assert launch_calls or "Browser error" in result
