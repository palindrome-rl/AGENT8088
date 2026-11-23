"""The verdict must not call work verified when the answer says one of its own checks failed and lets it stand.

Seen in a benchmark run: the model's browser test showed a FAIL in a screenshot, which no check of the run can read.
Its answer named the FAIL, called it a timing artifact of its test, and the footer said "Verification: exercised"
with no NOT VERIFIED headline. A grader found the remove button broken: every cart button acted on the last product.
"""
from agent8088 import engine
from agent8088.completion import reports_failed_check
from agent8088.trajectory import TrajectoryState

SEEN = (
    "Persistence works: the cart survived a reload.\n\n"
    "One note on the one `FAIL` in the harness log: the last `dec` click threw a null error, but that's a harness "
    "timing artifact (the page re-rendered between steps), not a page bug. The `dec` logic itself is correct.\n")


def test_a_fail_the_reply_explains_away_is_reported():
    assert reports_failed_check(SEEN)


def test_failed_tests_and_thrown_errors_are_reported():
    assert reports_failed_check("All but one scenario worked; 2 of 14 checks failed on the edge cases.")
    assert reports_failed_check("The restart step threw a TypeError, which I believe is unrelated.")
    assert reports_failed_check("The checkout test still failed on the last run.")


def test_no_failure_is_not():
    assert not reports_failed_check("All 21 checks pass, with no failures and no console errors.")
    assert not reports_failed_check("0 failed, 12 passed.")
    assert not reports_failed_check("Nothing failed in the click-through.")


def test_a_failure_that_was_fixed_is_not():
    assert not reports_failed_check("The first run had one FAIL on the discount; I fixed the rounding and it now passes.")
    assert not reports_failed_check("Two tests failed at first, then passed after the fix.")


def test_described_behaviour_is_not():
    assert not reports_failed_check("An invalid code would fail the validation and show `Invalid code`.")
    assert not reports_failed_check("If the server is down, the fetch step fails with a clear message.")


def test_an_answer_to_the_harness_failing_check_note_is_not():
    # The run's own checks already track that failure; the reply is answering the harness, not reporting its own.
    assert not reports_failed_check("Failing check: `set BUILD_FAST=1 && python build.py` (link error in module_077).")
    assert not reports_failed_check("What was actually failing: the recorded failing check was an early command in "
                                    "PowerShell syntax, which this shell does not run.")


def test_code_is_ignored():
    assert not reports_failed_check("Done.\n```\n1 FAIL\nAssertionError: expected 2\n```\nThat was before the fix; "
                                    "everything passes now.")


def _changed_and_run():
    state = TrajectoryState({}, goal="Make shop.html and try it in a browser.", message_count=1)
    state.data["mutation_count"] = 3
    state.data["ran_ok_at"] = 3
    return state


def test_the_headline_says_not_verified(monkeypatch):
    monkeypatch.setattr(engine, "NOT_VERIFIED_HEADLINE", True)
    answer = engine._with_task_notes(SEEN, _changed_and_run(), read_only=True)
    assert answer.startswith("NOT VERIFIED:")
    assert "the reply says one of its own checks failed" in answer.split("\n", 1)[0]


def test_a_clean_answer_keeps_its_level(monkeypatch):
    monkeypatch.setattr(engine, "NOT_VERIFIED_HEADLINE", True)
    answer = engine._with_task_notes("Done. shop.html loads; add, remove and the code all worked.",
                                     _changed_and_run(), read_only=True)
    assert not answer.startswith("NOT VERIFIED:")
