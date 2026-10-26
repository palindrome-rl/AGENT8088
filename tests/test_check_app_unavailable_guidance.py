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
    engine._check_app_guidance_state.given = False
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
    assert "browser check was unavailable" in out
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
])
def test_every_machine_side_reason_counts(note):
    assert app_check.environment_unavailable(app_check.Report("web", "p.html", app_check.UNAVAILABLE, note=note))


def test_page_timeout_is_not_evidence_of_a_machine_fault():
    report = app_check.Report("web", "p.html", app_check.UNAVAILABLE, note="the browser check did not finish in 15s")
    assert not app_check.environment_unavailable(report)
    assert app_check.browser_unavailable(report)
    assert "not a defect in the page" not in app_check.UNAVAILABLE_GUIDANCE


def test_a_new_request_gets_full_guidance_again(eng, monkeypatch):
    from types import SimpleNamespace as NS
    eng._check_app_guidance_state.given = True
    monkeypatch.setattr(eng, "_create_completion_with_fallback", lambda *a, **k:
                        NS(choices=[NS(message=NS(content="Done"), finish_reason="stop")]))
    eng.run_agent([{"role": "user", "content": "Hello"}], system_prompt="", tools_def=[],
                  allowed_tools=set(), max_turns=1, memory_capture=False)
    assert not eng._check_app_guidance_state.given


def test_a_page_that_hangs_is_not_declared_a_machine_fault(tmp_path):
    if app_check.chromium_executable() is None:
        pytest.skip("Chromium is not installed")
    page = tmp_path / "index.html"
    page.write_text("<html><script>while(true) {}</script></html>", encoding="utf-8")
    report = app_check.check_web_page(page, timeout=0.2, click_steps=0)
    assert report.status == app_check.UNAVAILABLE
    assert not app_check.environment_unavailable(report)
    assert app_check.browser_unavailable(report)


@pytest.mark.parametrize("report", [
    app_check.Report("web", "p.html", app_check.UNAVAILABLE, note="blocked external scripts prevented verification of JavaScript behavior"),
    app_check.Report("web", "p.html", app_check.UNAVAILABLE, note="a page error matched a missing backend reply; its cause was not verified"),
    app_check.Report("web", "p.html", app_check.PASS),
    app_check.Report("web", "p.html", app_check.FAIL, ["the page did not load: x"]),
    app_check.Report("script", "s.py", app_check.UNAVAILABLE, note="Chromium is not installed"),
])
def test_other_reports_get_no_guidance(report):
    assert not app_check.environment_unavailable(report)
