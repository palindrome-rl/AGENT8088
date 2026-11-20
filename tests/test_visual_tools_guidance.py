"""Planning and the browser-qa skill name the visual checks that actually work.

Seen in a live run: a plan written in plan mode never mentioned screenshot_page,
because plan mode only lists the tools it may call while planning. The approved
plan then checked the result by hand in a browser the agent could not drive, and
several turns went to that. The browser-qa skill had a similar gap: it told the
model to prove the main action with a Playwright script through execute_shell,
which cannot start a browser inside the Windows sandbox.
"""
from pathlib import Path

import pytest


def test_plan_mode_names_the_checks_the_plan_can_use(engine, monkeypatch):
    monkeypatch.setattr(engine, "TOOL_NAMES", set(engine.TOOL_NAMES) | {"check_app", "screenshot_page", "run_tests"})
    text = engine.render_permission_context("plan-only")
    assert "check_app" in text and "screenshot_page" in text and "run_tests" in text
    assert "after approval" in text.lower()


def test_plan_mode_leaves_out_checks_this_install_does_not_have(engine, monkeypatch):
    monkeypatch.setattr(engine, "TOOL_NAMES", set(engine.TOOL_NAMES) - {"screenshot_page"})
    text = engine.render_permission_context("plan-only")
    assert "screenshot_page" not in text
    assert "check_app" in text


def test_plan_mode_with_no_checks_says_nothing_about_them(engine, monkeypatch):
    monkeypatch.setattr(engine, "TOOL_NAMES", set(engine.TOOL_NAMES)
                        - {"check_app", "screenshot_page", "view_image", "run_tests"})
    text = engine.render_permission_context("plan-only")
    assert "after approval" not in text.lower()


@pytest.mark.parametrize("mode", ["readonly", "edit", "full-auto"])
def test_other_modes_are_unchanged(engine, mode):
    assert "after approval" not in engine.render_permission_context(mode).lower()


def _skill(engine) -> str:
    path = Path(engine.__file__).parent / "skills_installed" / "browser-qa" / "SKILL.md"
    return path.read_text(encoding="utf-8")


def test_browser_qa_proves_the_action_with_check_app_first(engine):
    text = _skill(engine)
    row = next(line for line in text.splitlines() if line.startswith("| Does the main action work?"))
    assert "check_app" in row


def test_browser_qa_does_not_send_the_model_to_a_browser_it_cannot_start(engine):
    text = _skill(engine)
    assert "spawn EPERM" in text or "cannot start" in text
    assert "not verified" in text
    # A test script is still allowed, but only as an optional extra.
    if "sync_playwright" in text:
        assert text.index("check_app") < text.index("sync_playwright")
