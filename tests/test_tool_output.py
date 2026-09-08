"""Tool output is denoised, then truncated tail-first, then given a real verdict.

The failure this guards against: a test run's summary line sits at the very end
of the output, head-only truncation dropped it, and the model was left with a
run of PASSED lines that reads as a clean pass.
"""
import pytest

from agent8088 import tool_output


# --------------------------------------------------------------------------
# Step 1: denoise before measuring length
# --------------------------------------------------------------------------

def test_ansi_colour_codes_are_stripped():
    assert tool_output.denoise("\x1b[31mFAILED\x1b[0m tests") == "FAILED tests"


def test_carriage_return_progress_keeps_only_the_final_state():
    # A progress bar rewrites one line; only the last state is information.
    noisy = "Installing... 10%\rInstalling... 60%\rInstalling... done\n"
    assert tool_output.denoise(noisy) == "Installing... done"


def test_consecutive_identical_lines_collapse_with_a_count():
    noisy = "\n".join(["warning: unused import"] * 40) + "\nDone"
    clean = tool_output.denoise(noisy)
    assert clean.count("warning: unused import") == 1
    assert "repeated 40 times" in clean
    assert clean.endswith("Done")


def test_runs_of_blank_lines_collapse_to_one():
    assert tool_output.denoise("a\n\n\n\n\n\nb") == "a\n\nb"


def test_trailing_whitespace_goes_away():
    assert tool_output.denoise("a   \nb\t\n") == "a\nb"


def test_already_clean_text_is_unchanged():
    clean = "test_a PASSED\ntest_b FAILED\n\n1 failed, 1 passed"
    assert tool_output.denoise(clean) == clean


def test_denoise_is_idempotent():
    noisy = "\x1b[32mok\x1b[0m   \n\n\n\nok\nok\nok\n"
    once = tool_output.denoise(noisy)
    assert tool_output.denoise(once) == once


def test_denoising_alone_can_bring_output_under_the_limit():
    # 400 identical noisy lines: far over any budget raw, tiny once collapsed.
    noisy = "\n".join(["\x1b[33mwarn: deprecated\x1b[0m"] * 400)
    assert len(noisy) > 3_000
    assert len(tool_output.denoise(noisy)) < 3_000


# --------------------------------------------------------------------------
# Step 2: keep both ends, weighted toward the end
# --------------------------------------------------------------------------

def test_text_within_budget_is_returned_untouched():
    assert tool_output.clamp("short", limit=100) == "short"


def test_clamp_keeps_the_end_where_the_verdict_lives():
    body = "\n".join(f"test_{i} ... ok" for i in range(600))
    text = body + "\nFAILED (failures=1)"
    shown = tool_output.clamp(text, limit=1_000)

    assert shown.endswith("FAILED (failures=1)")
    assert shown.startswith("test_0 ... ok")
    assert "elided" in shown


def test_clamp_gives_most_of_the_budget_to_the_tail():
    text = "H" * 5_000 + "T" * 5_000
    shown = tool_output.clamp(text, limit=1_000)
    assert shown.count("T") > shown.count("H") * 2


def test_clamp_respects_the_budget():
    shown = tool_output.clamp("x" * 100_000, limit=3_000)
    assert len(shown) <= 3_000 + 200  # payload plus the one-line marker


def test_head_mode_keeps_only_the_beginning_for_top_down_reads():
    text = "line one\n" + "filler\n" * 2_000 + "last line"
    shown = tool_output.clamp(text, limit=500, keep="head")
    assert shown.startswith("line one")
    assert "last line" not in shown


def test_clamp_reports_how_much_it_elided():
    text = "A" * 10_000
    shown = tool_output.clamp(text, limit=1_000)
    head, _, rest = shown.partition("[")
    count, _, tail = rest.partition("]")
    dropped = int("".join(c for c in count if c.isdigit()))
    # The marker sits on its own line; those separators are not payload.
    kept = len(head.rstrip("\n")) + len(tail.lstrip("\n"))
    assert dropped == len(text) - kept


# --------------------------------------------------------------------------
# Step 3: a real verdict, or an honest "unknown"
# --------------------------------------------------------------------------

@pytest.mark.parametrize("output,status", [
    ("===== 1 failed, 39 passed in 0.02s =====", "failed"),
    ("===== 40 passed in 0.02s =====", "passed"),
    ("===== 12 passed, 3 skipped in 1.1s =====", "passed"),
    ("Ran 80 tests in 0.002s\n\nFAILED (failures=1)", "failed"),
    ("Ran 80 tests in 0.002s\n\nOK", "passed"),
    ("Tests:       2 failed, 10 passed, 12 total", "failed"),
    ("Tests:       12 passed, 12 total", "passed"),
    ("test result: FAILED. 3 passed; 1 failed;", "failed"),
    ("test result: ok. 4 passed; 0 failed;", "passed"),
])
def test_known_runner_summaries_are_read_exactly(output, status):
    assert tool_output.detect_verdict(output).status == status


def test_nonzero_exit_status_is_a_failure_even_without_a_summary():
    v = tool_output.detect_verdict("some build noise\nCommand exited with status 2.")
    assert v.status == "failed"


def test_prose_without_a_summary_is_unknown_not_a_guess():
    for text in ("all good here", "", "Command completed.", "3 files changed"):
        assert tool_output.detect_verdict(text).status == "unknown"


def test_the_word_passed_in_passing_prose_does_not_make_a_verdict():
    # A single "PASSED" line is one test, not the suite's result.
    assert tool_output.detect_verdict("test_calc.py::test_add PASSED").status == "unknown"


def test_no_tests_collected_is_unknown_not_passed():
    assert tool_output.detect_verdict("no tests ran in 0.01s").status == "unknown"


def test_verdict_carries_the_summary_line_it_read():
    v = tool_output.detect_verdict("lots\nof\nnoise\n==== 1 failed, 39 passed in 1s ====")
    assert "1 failed, 39 passed" in v.summary


def test_verdict_survives_being_read_from_denoised_coloured_output():
    coloured = "\x1b[31m==== 1 failed, 2 passed in 1s ====\x1b[0m"
    assert tool_output.detect_verdict(tool_output.denoise(coloured)).status == "failed"


# --------------------------------------------------------------------------
# Step 5: the full text is kept and the middle is retrievable
# --------------------------------------------------------------------------

def test_store_keeps_the_whole_text_and_hands_back_the_middle():
    store = tool_output.OutputStore()
    text = "HEAD" + "M" * 5_000 + "TAIL"
    ref = store.put("execute_shell", text)

    assert store.get(ref) == text
    window = store.window(ref, offset=4, length=100)
    assert window["text"] == "M" * 100
    assert window["total"] == len(text)


def test_window_past_the_end_says_so_instead_of_erroring():
    store = tool_output.OutputStore()
    ref = store.put("execute_shell", "short")
    window = store.window(ref, offset=9_999, length=50)
    assert window["text"] == ""
    assert window["total"] == 5


def test_unknown_reference_is_reported_not_raised():
    store = tool_output.OutputStore()
    assert store.get("no-such-ref") is None
