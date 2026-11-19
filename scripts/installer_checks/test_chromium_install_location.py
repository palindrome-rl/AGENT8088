"""Browser checks use the installed browser and do not remember a missing browser forever."""
import pytest

from agent8088 import app_check


def test_a_missing_browser_is_probed_again(monkeypatch, tmp_path):
    pytest.importorskip('playwright.sync_api')
    executable = tmp_path / 'chrome.exe'
    executable.write_bytes(b'')
    answers = iter([None, str(executable)])
    monkeypatch.setattr(app_check, 'locate_chromium', lambda: next(answers))
    assert not app_check.chromium_available()
    assert app_check.chromium_available()


def test_check_launches_the_engine_selected_executable(engine, monkeypatch, tmp_path):
    pytest.importorskip('playwright.sync_api')
    executable = tmp_path / 'playwright-browsers' / 'chrome.exe'
    executable.parent.mkdir()
    executable.write_bytes(b'')
    monkeypatch.setattr(engine, '_playwright_chromium_executable', lambda: str(executable))
    assert app_check.chromium_executable() == str(executable)
    launched = []

    def worker(*args):
        launched.append(args[-1])
        args[4]['error'] = 'synthetic worker stopped before browser launch'

    monkeypatch.setattr(app_check, '_web_worker', worker)
    page = tmp_path / 'index.html'
    page.write_text('<!doctype html><title>test</title>', encoding='utf-8')
    app_check.check_web_page(page)
    assert launched == [str(executable)]
