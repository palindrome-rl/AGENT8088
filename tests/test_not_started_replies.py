"""A reply before any tool call that is empty or only says what comes next has not started the work.

Seen in a benchmark run: the first reply was empty, the harness asked for "your final answer in plain text ... do not
call any tools", the model answered "Let me read the data file first." three times, each one went to the completion
checks, and the run ended in 16 seconds with nothing done. The model is now asked to start, outside the completion
budget, a bounded number of times."""
import pytest

from agent8088 import completion as cc
from tests.test_completion_pipeline import (  # noqa: F401
    Script,
    _call,
    eng,
    failing_then_ok,
    run,
)


@pytest.mark.parametrize("reply", [
    "Let me read the data file first.",
    "I'll read the data first, then build and verify the page.",
    "Okay, let me start by reading the data file.",
    "First, I'll look at the fixture.",
    "I'm going to inspect the build script.",
    "Starting with the data file.",
])
def test_replies_that_only_announce_a_step(reply):
    assert cc.only_announces_next_step(reply)


@pytest.mark.parametrize("reply", [
    "The page is in report.html and shows all twelve months.",
    "I could not build it: npm is not installed here.",
    "Let me explain: " + "the build links every module, then writes the manifest. " * 10,
    "I'll note that:\n```\nnpm run build\n```",
    "",
])
def test_answers_are_not_announcements(reply):
    assert not cc.only_announces_next_step(reply)


@pytest.mark.parametrize("request_text,work", [
    ("Build an offline report page from data.json", True),
    ("fix the link error in module_077", True),
    ("Write the summary to report.md", True),
    ("hi", False),
    ("how does the build script work?", False),
    ("What is in data.json?", False),
])
def test_asks_for_work(request_text, work):
    assert cc.asks_for_work(request_text) is work


def test_an_empty_first_reply_is_asked_to_start_not_to_answer(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        "",
        _call("execute_shell", command="make"),
        "Built.",
    ], tool=failing_then_ok(["built"]))
    answer = run(eng, "build the project", script)
    assert len(script.nudges("No tool has been called yet")) == 1
    assert not script.nudges("do not call any tools")
    assert answer.startswith("Built.")


def test_announcing_replies_get_the_start_ask_and_keep_the_completion_budget(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        "Let me read the data file first.",
        "I'll read the data file to see its structure.",
        _call("execute_shell", command="make"),
        "Built.",
    ], tool=failing_then_ok(["built"]))
    answer = run(eng, "build the project", script)
    assert len(script.nudges("No tool has been called yet")) == 2
    assert answer.startswith("Built.")


def test_the_start_ask_is_bounded(eng, monkeypatch):
    script = Script(eng, monkeypatch, ["Let me read the data file first."])
    answer = run(eng, "build the project", script)
    assert len(script.nudges("No tool has been called yet")) == eng.NOT_STARTED_ASKS == 2
    assert script.calls < 12
    assert answer


def test_after_work_has_started_an_announcement_is_left_to_the_completion_checks(eng, monkeypatch):
    script = Script(eng, monkeypatch, [
        _call("execute_shell", command="make"),
        "Built.",
    ], tool=failing_then_ok(["built"]))
    run(eng, "build the project", script)
    assert not script.nudges("No tool has been called yet")


def test_a_greeting_with_an_empty_reply_keeps_the_answer_retry(eng, monkeypatch):
    script = Script(eng, monkeypatch, ["", "Hello!"])
    assert run(eng, "hi", script) == "Hello!"
    assert not script.nudges("No tool has been called yet")
