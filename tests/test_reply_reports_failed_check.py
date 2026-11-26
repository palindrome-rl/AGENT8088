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


def test_the_word_failure_about_the_original_bug_is_not():
    # Seen in a benchmark run: a fix whose full build passed was marked NOT VERIFIED because the reply said how it
    # reproduced the bug it was asked to fix. "the failure" is the bug, not a check of the model's that failed.
    assert not reports_failed_check("First ran `BUILD_FAST=1 python build.py` to quickly reproduce the failure and "
                                    "pinpoint the exact link error.")
    assert not reports_failed_check("The failure came from a typo in module_077, which I corrected.")
    assert not reports_failed_check("A failure in the link step stopped the build before my change.")


def test_the_failing_test_the_task_is_about_is_not():
    # Seen in a benchmark run: "Off-by-one at the 5 kg boundary (the failing test)." named the test the task gave,
    # which the fix made pass, and the run ended NOT VERIFIED.
    assert not reports_failed_check("Off-by-one at the 5 kg boundary (the failing test).")
    assert not reports_failed_check("All 8 tests pass, including the previously failing one.")
    assert not reports_failed_check("The originally failing test now passes along with the rest.")


def test_your_failing_test_is_the_task_s_test():
    # Seen in a benchmark run: "the bug your failing test caught" named the test the task gave.
    assert not reports_failed_check("1. weight_kg < 5 -> weight_kg <= 5, the bug your failing test caught.")
    assert not reports_failed_check("This failing test now passes with the rest.")


def test_a_section_about_the_original_bug_is_not_a_failed_check():
    # Seen in a benchmark run: a reply under "What was wrong" said "The link step failed because ..." about the bug
    # it fixed; the full build passed and the run ended NOT VERIFIED.
    for heading in ("## What was wrong", "**What was wrong**", "### Root cause", "**The bug:**", "## Problem"):
        reply = (f"Done. The build passes now.\n\n{heading}\nThe link step failed because module_077 used "
                 "parse_heder.\n\n## What I changed\nOne line in module_077.txt.")
        assert not reports_failed_check(reply), heading


def test_a_cause_section_that_says_it_still_fails_counts():
    reply = "## What was wrong\nThe link step still failed after my change, on module_078.\n"
    assert reports_failed_check(reply)


def test_the_same_sentence_outside_a_cause_section_counts():
    assert reports_failed_check("## Result\nThe link step failed because module_078 is missing a symbol.")


def test_past_tense_descriptions_are_not_a_failed_check():
    assert not reports_failed_check("The test was failing because of the off-by-one; it passes now.")
    assert not reports_failed_check("The checkout test used to fail on reload.")
    assert not reports_failed_check("The step that originally failed is the link step.")


def test_the_failing_test_that_still_fails_counts():
    assert reports_failed_check("The failing test still fails on the 10 kg case.")
    assert reports_failed_check("Two failing tests remain in the edge cases.")


def test_an_uppercase_fail_marker_still_counts():
    assert reports_failed_check("The log shows the FAIL on the dec click, which I think is a timing issue.")
    assert reports_failed_check("2 failures remain in the edge cases.")


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
