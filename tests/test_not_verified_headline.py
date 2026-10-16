"""A line at the top of the answer when the run saw a clear sign that the work is unfinished or not working."""
import pytest

from agent8088 import trajectory


def _state(**data):
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.data.update(data)
    return state


def test_a_failed_page_check_puts_the_headline_first(engine):
    state = _state(smoke_notes=["index.html: smoke test FAILED - a page error"])
    answer = engine._with_task_notes("The task is complete.", state)
    assert answer.startswith("NOT VERIFIED: the page/script check failed (index.html). Details are in the notes at the end.")
    assert "The task is complete." in answer and "Smoke test: index.html: smoke test FAILED" in answer


@pytest.mark.parametrize("data, expected", [
    ({"check_failing": True, "failing_check": "python test_app.py -> 1 failed"}, "the latest check failed"),
    ({"partial_files": [{"path": "/w/app.js"}]}, "a file was cut off"),
    ({"unmet_checks": ["missing or empty files: out.txt"]}, "missing or empty files: out.txt"),
    ({"unmet_checks": ["the reply says the work is unfinished"]}, "the reply says the work is unfinished"),
    ({"unmet_checks": ["a credential is written in clear text in: config.py"]}, "a credential is written in clear text"),
])
def test_each_strong_sign_alone_gives_the_headline(engine, data, expected):
    answer = engine._with_task_notes("Done.", _state(**data))
    assert answer.startswith("NOT VERIFIED:") and expected in answer.split("\n\n")[0]


def test_open_parts_of_the_request_give_the_headline(engine):
    state = _state()
    state.set_subgoals(["make a thing", "write the readme"])
    answer = engine._with_task_notes("Done.", state)
    assert answer.startswith("NOT VERIFIED: 2 part(s) of the request not addressed")


@pytest.mark.parametrize("data", [
    {},
    {"smoke_notes": ["app.js: smoke test unavailable (parses; browser context is required)"]},
    {"smoke_notes": ["index.html: smoke test passed"]},
    {"unmet_checks": ["changed work has no passing check"]},
    {"unmet_checks": ["library files changed without tests: parser.py"]},
])
def test_the_common_notes_on_correct_work_give_no_headline(engine, data):
    answer = engine._with_task_notes("Done.", _state(**data))
    assert not answer.startswith("NOT VERIFIED:")


def test_the_headline_appears_once_and_can_be_switched_off(engine, monkeypatch):
    state = _state(check_failing=True, failing_check="x")
    once = engine._with_task_notes("Done.", state)
    assert engine._with_task_notes(once, state).count("NOT VERIFIED:") == 1
    monkeypatch.setattr(engine, "NOT_VERIFIED_HEADLINE", False)
    assert not engine._with_task_notes("Done.", state).startswith("NOT VERIFIED:")


def test_no_trajectory_means_no_headline(engine):
    assert engine._with_task_notes("Done.", None) == "Done."
