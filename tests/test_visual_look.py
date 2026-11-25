"""Two small aids for work that is meant to be seen.

1. When a request is about how the result looks and the run built a page or image, one nudge (per state of the work, at most twice a
   request) asks the model to render it and look before the final answer, if nothing in the run has. It never blocks and never makes a
   run "not completed".
2. The `screenshot_page` result can carry a short "Layout notes" line: the page is larger than the window, text that is hard to read
   against its background, text under 10 px. Nothing about taste, and nothing when there is nothing to say.
"""
import json

import pytest

from agent8088 import app_check, completion as c, render_check
from agent8088.trajectory import TrajectoryState

VISUAL = "Build a polished landing page with a clear layout and a warm colour palette."
PLAIN = "Write a page that lists the files in a folder."
PAGE = "<!doctype html><html><body><h1>Hello</h1></body></html>"


@pytest.fixture
def workspace(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "COMPLETION_VISUAL_LOOK", True)
    engine._screenshot_counts.clear()
    return tmp_path


def _state(workspace, request=VISUAL, files=("index.html",)):
    state = TrajectoryState({}, goal=request, message_count=1, workspace=str(workspace))
    for name in files:
        (workspace / name).write_text(PAGE if name.endswith((".html", ".svg")) else "print(1)\n", encoding="utf-8")
        operation = state.before_tool("write_file")
        state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown",
                         path=str(workspace / name))
    return state


def _written(state):
    return state.landed_paths()


# ----------------------------------------------------------------------------- the request

@pytest.mark.parametrize("request_text", [
    "Build a polished landing page.",
    "Make the layout responsive and the colours warm.",
    "Create an animated pixel-art scene with original artwork.",
    "It should look professional and the user interface should be clear.",
])
def test_requests_about_how_it_looks_are_visual(request_text):
    assert c.wants_visual_result(request_text)


@pytest.mark.parametrize("request_text", [
    "Write a script that renames files by date.",
    "Fix the failing test in parser.py.",
    "Summarise this report in three bullet points.",
    "",
])
def test_other_requests_are_not(request_text):
    assert not c.wants_visual_result(request_text)


# ----------------------------------------------------------------------------- when the nudge is asked

def test_a_page_built_for_a_visual_request_that_nobody_looked_at_gets_the_ask(engine, workspace):
    state = _state(workspace)
    nudge = engine._visual_look_step(state, _written(state), VISUAL)
    assert nudge is not None and nudge.kind == "look"
    assert "screenshot_page" in nudge.text and "say so in one line" in nudge.text
    assert nudge.unmet == ""                                 # asking never makes the run "not completed"


def test_it_is_asked_once_for_a_state_of_the_work(engine, workspace):
    state = _state(workspace)
    assert engine._visual_look_step(state, _written(state), VISUAL) is not None
    assert engine._visual_look_step(state, _written(state), VISUAL) is None


def test_not_when_the_run_already_looked_at_this_state(engine, workspace):
    state = _state(workspace)
    state.note_looked()
    assert engine._visual_look_step(state, _written(state), VISUAL) is None


def test_a_change_after_the_look_asks_again_but_only_twice_a_request(engine, workspace):
    state = _state(workspace)
    state.note_looked()
    asks = 0
    for _ in range(4):
        operation = state.before_tool("edit_file")                    # a change since the last look
        state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown",
                         path=str(workspace / "index.html"))
        asks += engine._visual_look_step(state, _written(state), VISUAL) is not None
    assert asks == 2


def test_a_new_request_starts_the_count_over(engine, workspace):
    state = _state(workspace)
    engine._visual_look_step(state, _written(state), VISUAL)
    assert state.data["look_asks"] == 1
    again = TrajectoryState(state.data, goal="Now make it look warmer.", message_count=5, workspace=str(workspace))
    assert again.data["look_asks"] == 0


def test_not_for_a_request_that_is_not_about_how_it_looks(engine, workspace):
    state = _state(workspace, request=PLAIN)
    assert engine._visual_look_step(state, _written(state), PLAIN) is None


def test_not_when_nothing_to_look_at_was_built(engine, workspace):
    state = _state(workspace, files=("tool.py",))
    assert engine._visual_look_step(state, _written(state), VISUAL) is None


def test_not_when_the_setting_is_off(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "COMPLETION_VISUAL_LOOK", False)
    state = _state(workspace)
    assert engine._visual_look_step(state, _written(state), VISUAL) is None


def test_it_comes_last_and_only_at_the_top_level(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "COMPLETION_SMOKE_TEST", "off")
    state = _state(workspace)
    state.data["verification"] = "inspected"
    budget = engine.completion_checks.ContinuationBudget(limit=3)
    nested = engine._next_completion_nudge(budget, state, reply="Done, it is built.", request=VISUAL, depth=1)
    assert nested is None
    nudge = engine._next_completion_nudge(budget, state, reply="Done, it is built.", request=VISUAL, depth=0)
    assert nudge is not None and nudge.kind == "look"
    assert budget.unmet == []


def test_a_more_important_check_goes_first(engine, workspace, monkeypatch):
    monkeypatch.setattr(engine, "COMPLETION_SMOKE_TEST", "off")
    state = _state(workspace)
    budget = engine.completion_checks.ContinuationBudget(limit=3)
    nudge = engine._next_completion_nudge(
        budget, state, reply="Not done yet - I still need to write the styles.", request=VISUAL, depth=0)
    assert nudge is not None and nudge.kind == "unfinished"


# ----------------------------------------------------------------------------- the layout note

def test_nothing_to_say_is_an_empty_note():
    assert render_check.layout_note({}) == ""
    assert render_check.layout_note({"vw": 1280, "vh": 720, "scrollW": 1280, "scrollH": 720, "lowCount": 0, "tiny": 0}) == ""


def test_a_page_larger_than_the_window_says_so():
    note = render_check.layout_note({"vw": 1280, "vh": 720, "scrollW": 1280, "scrollH": 1500})
    assert "larger than the window (1280 x 1500 in 1280 x 720)" in note and "scrolls" in note


def test_a_pixel_of_slack_is_not_a_scrollbar():
    assert render_check.layout_note({"vw": 1280, "vh": 720, "scrollW": 1281, "scrollH": 721}) == ""


def test_hard_to_read_and_tiny_text_are_counted_with_one_example():
    note = render_check.layout_note({"vw": 1280, "vh": 720, "scrollW": 1280, "scrollH": 720,
                                     "lowCount": 2, "low": [{"t": "Press space", "r": 2.1}], "tiny": 1})
    assert '2 text items hard to read against the background (for example "Press space" at 2.1:1)' in note
    assert "1 text item under 10 px" in note


def test_a_broken_probe_result_is_ignored():
    assert render_check.layout_note(None) == ""
    assert render_check.layout_note("nope") == ""


# ----------------------------------------------------------------------------- the screenshot tool (needs Chromium)

@pytest.fixture
def chromium():
    if not app_check.chromium_available():
        pytest.skip("Chromium for Playwright is not installed")


needs_chromium = pytest.mark.usefixtures("chromium")


def _shoot(engine, path):
    result = engine.run_tool("screenshot_page", {"path": path})
    assert result.startswith(engine._IMAGE_MARKER_PREFIX), result
    return engine._message_text(json.loads(result[len(engine._IMAGE_MARKER_PREFIX):])["message"])


CLEAN = ("<!doctype html><html><body style='margin:0;background:#102030;color:#f4f4f4;font:18px sans-serif'>"
         "<p style='padding:20px'>A readable line of text.</p></body></html>")
TALL = ("<!doctype html><html><body style='margin:0;background:#fff;color:#111;font:18px sans-serif'>"
        "<div style='height:2400px'>tall</div></body></html>")
FAINT = ("<!doctype html><html><body style='margin:0;background:#ffffff;color:#111;font:18px sans-serif'>"
         "<p style='color:#dddddd'>Very faint instruction</p><p style='font-size:8px'>tiny print</p></body></html>")


@needs_chromium
def test_a_clean_page_gets_no_layout_line(engine, workspace):
    (workspace / "ok.html").write_text(CLEAN, encoding="utf-8")
    assert "Layout notes" not in _shoot(engine, "ok.html")


@needs_chromium
def test_a_tall_page_is_reported_as_scrolling(engine, workspace):
    (workspace / "tall.html").write_text(TALL, encoding="utf-8")
    assert "Layout notes: the page is larger than the window" in _shoot(engine, "tall.html")


@needs_chromium
def test_faint_and_tiny_text_are_reported(engine, workspace):
    (workspace / "faint.html").write_text(FAINT, encoding="utf-8")
    text = _shoot(engine, "faint.html")
    assert "hard to read against the background" in text and "Very faint instruction" in text
    assert "under 10 px" in text


@needs_chromium
def test_an_image_file_gets_no_layout_line(engine, workspace):
    (workspace / "shape.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60"><rect width="80" height="60" fill="#c0392b"/></svg>',
        encoding="utf-8")
    assert "Layout notes" not in _shoot(engine, "shape.svg")


@needs_chromium
def test_a_screenshot_in_the_loop_counts_as_a_look(engine, workspace, monkeypatch):
    (workspace / "ok.html").write_text(CLEAN, encoding="utf-8")
    replies = ['✿FUNCTION✿: screenshot_page ✿ARGS✿: {"path": "ok.html"}', "It looks fine."]
    calls = []

    def completion(messages, *_a, **_k):
        calls.append(True)
        message = type("Message", (), {"content": replies[min(len(calls) - 1, 1)]})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    monkeypatch.setattr(engine, "_model_accepts_images", lambda: True)
    holder = {}
    engine.run_agent([{"role": "user", "content": "Check how ok.html looks."}], max_turns=4, tools_def=[],
                     allowed_tools={"screenshot_page"}, system_prompt="", memory_capture=False, trajectory_state=holder)
    assert holder.get("look_seen_at") is not None
