"""A run ended by the closing cap still gets one report-only look at the page or script it wrote.

The cap skips the completion checks on purpose (they are what it exists to stop), which used to leave a page
that does not load with no note at all. The look adds no round and no follow-up.
"""
import json
from pathlib import Path
from types import SimpleNamespace as N

import pytest

from agent8088 import app_check, completion as completion_checks, trajectory


def _response(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _calc(i):
    return f'✿FUNCTION✿: calculate ✿ARGS✿: {{"expression": "{i}+1"}}'


def _write(page):
    return f'✿FUNCTION✿: write_file ✿ARGS✿: {json.dumps({"filename": str(page), "content": "<html></html>"})}'


@pytest.fixture
def eng(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    monkeypatch.setattr(engine, "MAX_POST_CHECK_ROUNDS", 3)
    return engine


def _stub_check(monkeypatch, status):
    seen = []

    def check(path, timeout=20, deny=None, **_kw):
        seen.append(Path(path).name)
        failures = ["the first control (button \"Go\") is covered by div#veil"] if status == app_check.FAIL else []
        return app_check.Report("web", str(path), status, failures)

    monkeypatch.setattr(app_check, "check_target", check)
    return seen


def _run_to_the_cap(engine, monkeypatch, tmp_path, request="Build the page and save the result to out.txt"):
    """Write a page, then answer; a named file is still missing, so the smoke step is never reached before the cap."""
    page = tmp_path / "index.html"
    calls = []

    def model(messages, tools, **kwargs):
        calls.append(messages)
        last = str(messages[-1].get("content"))
        if "Checks are complete" in last:
            return _response("I am done.")
        i = len(calls) - 1
        return _response(_write(page) if i == 0 else "All done." if i == 1 else _calc(i))

    def tool(name, arguments, depth=0):
        if name == "write_file":
            engine.note_mutation(name)
            page.write_text("<html></html>", encoding="utf-8")
            return "Wrote 13 bytes"
        return "2"

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "exec_tool", tool)
    return engine.run_agent([{"role": "user", "content": request}], max_turns=40, dynamic_turns=False), calls


def test_a_page_that_fails_is_noted_when_the_cap_ends_the_run(eng, monkeypatch, tmp_path):
    seen = _stub_check(monkeypatch, app_check.FAIL)
    answer, calls = _run_to_the_cap(eng, monkeypatch, tmp_path)
    assert "I am done." in answer
    assert seen == ["index.html"], "checked once"
    assert "Smoke test: index.html: smoke test FAILED" in answer
    assert not any("smoke test of what you built failed" in str(m[-1].get("content")) for m in calls), "no follow-up"


def test_a_passing_page_gets_the_short_note(eng, monkeypatch, tmp_path):
    _stub_check(monkeypatch, app_check.PASS)
    answer, _ = _run_to_the_cap(eng, monkeypatch, tmp_path)
    assert "index.html: smoke test passed" in answer


def test_the_check_can_be_switched_off(eng, monkeypatch, tmp_path):
    seen = _stub_check(monkeypatch, app_check.FAIL)
    monkeypatch.setattr(eng, "COMPLETION_SMOKE_TEST", "off")
    answer, _ = _run_to_the_cap(eng, monkeypatch, tmp_path)
    assert seen == [] and "Smoke test" not in answer


def test_a_check_that_cannot_run_never_takes_the_ending_down(eng, monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("browser missing")

    monkeypatch.setattr(app_check, "check_target", boom)
    answer, _ = _run_to_the_cap(eng, monkeypatch, tmp_path)
    assert "I am done." in answer


def test_sub_agents_are_not_checked(eng, monkeypatch, tmp_path):
    seen = _stub_check(monkeypatch, app_check.FAIL)
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.data["written_paths"] = [str(tmp_path / "index.html")]
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    budget = completion_checks.ContinuationBudget(limit=3)
    eng._record_unmet_checks(budget, state, reply="done", request="build a page", depth=1)
    assert seen == []
    eng._record_unmet_checks(budget, state, reply="done", request="build a page", depth=0)
    assert seen == ["index.html"]
    eng._record_unmet_checks(budget, state, reply="done", request="build a page", depth=0)
    assert seen == ["index.html"], "unchanged work is not checked twice"
