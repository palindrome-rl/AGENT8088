"""When check_app cannot open a browser (Chromium missing, it will not start, it hangs) the model used to read one line,
"smoke test unavailable (Chromium is not installed...)", conclude it could not see the app, and spend many turns on Node
scripts that stub the DOM. The result now says it is a limit of the machine and what to do instead; a page check that is
unavailable for any other reason, a pass and a failure are untouched."""
import json

import pytest

from agent8088 import app_check
from tests.test_app_check import WORKING


@pytest.fixture
def eng(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    engine._check_app_guidance_given[0] = False
    return engine


def _page(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(WORKING, encoding="utf-8")
    return str(page)


def test_missing_chromium_says_why_and_what_to_do(eng, tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "chromium_executable", lambda: None)
    out = eng.exec_tool("check_app", json.dumps({"path": _page(tmp_path)}))
    assert "smoke test unavailable" in out
    assert "python -m playwright install chromium" in out
    assert "limit of this machine, not a defect in the page" in out
    assert "Do not build DOM or browser emulators" in out and "node --check" in out
    assert "not loaded in a browser" in out


def test_the_guidance_is_full_once_and_short_after(eng, tmp_path, monkeypatch):
    monkeypatch.setattr(app_check, "chromium_executable", lambda: None)
    first = eng.exec_tool("check_app", json.dumps({"path": _page(tmp_path)}))
    second = eng.exec_tool("check_app", json.dumps({"path": _page(tmp_path)}))
    assert app_check.UNAVAILABLE_GUIDANCE in first
    assert app_check.UNAVAILABLE_GUIDANCE not in second and app_check.UNAVAILABLE_GUIDANCE_SHORT in second


def test_missing_chromium_reports_the_browser_as_degraded(eng, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(app_check, "chromium_executable", lambda: None)
    monkeypatch.setattr(eng, "_report_browser", lambda missing="": seen.append(missing))
    eng.exec_tool("check_app", json.dumps({"path": _page(tmp_path)}))
    assert seen == ["chromium"]


@pytest.mark.parametrize("note", [
    "Chromium is not installed for Agent8088, so no page can be loaded",
    "the browser could not run: Target closed",
    "the browser check did not finish in 75s",
])
def test_every_machine_side_reason_counts(note):
    assert app_check.environment_unavailable(app_check.Report("web", "p.html", app_check.UNAVAILABLE, note=note))


@pytest.mark.parametrize("report", [
    app_check.Report("web", "p.html", app_check.UNAVAILABLE, note="blocked external scripts prevented verification of JavaScript behavior"),
    app_check.Report("web", "p.html", app_check.UNAVAILABLE, note="a page error matched a missing backend reply; its cause was not verified"),
    app_check.Report("web", "p.html", app_check.PASS),
    app_check.Report("web", "p.html", app_check.FAIL, ["the page did not load: x"]),
    app_check.Report("script", "s.py", app_check.UNAVAILABLE, note="Chromium is not installed"),
])
def test_other_reports_get_no_guidance(report):
    assert not app_check.environment_unavailable(report)
