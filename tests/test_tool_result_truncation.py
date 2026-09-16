"""Tool results must keep their tail: that is where the verdict lives.

Head-only truncation dropped pytest's "1 failed, 39 passed" summary, the
"Command exited with status 1" line, and the traceback of whatever failed —
leaving the model a run of PASSED lines that reads as a clean pass.
"""


def _verdict_tail(passed: int = 39) -> str:
    return (
        "=========================== short test summary info ===================\n"
        "FAILED test_calc.py::test_discount_tiered - assert 180.0 == 170.0\n"
        f"========================= 1 failed, {passed} passed in 0.02s ==========\n"
        "Command exited with status 1."
    )


def _long_run(passed: int = 39) -> str:
    body = "\n".join(f"test_calc.py::test_add[{i}] PASSED    [ {i}%]"
                     for i in range(passed))
    return body + "\n" + _verdict_tail(passed)


def test_short_result_is_returned_untouched(engine):
    result = "1 failed, 39 passed in 0.02s"
    assert engine._tool_result_for_model("execute_shell", result) == result


def test_verdict_at_the_end_survives_truncation(engine):
    run = _long_run(passed=400)
    shown = engine._tool_result_for_model("execute_shell", run)

    assert len(shown) < len(run), "test data must be long enough to truncate"
    assert "1 failed, 400 passed" in shown
    assert "Command exited with status 1." in shown
    assert "FAILED test_calc.py::test_discount_tiered" in shown


def test_the_verdict_is_stated_before_the_payload(engine):
    run = _long_run(passed=400)
    shown = engine._tool_result_for_model("execute_shell", run)
    # First line, so no budget pressure can push it out and the model does not
    # have to find a summary line to know the answer.
    assert shown.splitlines()[0].startswith("[verdict: FAILED]")


def test_beginning_of_the_payload_also_survives(engine):
    run = _long_run(passed=400)
    shown = engine._tool_result_for_model("execute_shell", run)
    body = shown.split("\n", 1)[1]
    assert body.startswith("test_calc.py::test_add[0] PASSED")


def test_truncation_is_announced_in_the_middle_with_a_count(engine):
    run = _long_run(passed=400)
    shown = engine._tool_result_for_model("execute_shell", run)

    assert "[tool result truncated:" in shown
    head, _, rest = shown.partition("[tool result truncated:")
    marker, _, tail = rest.partition("]")

    assert head, "head must come before the marker"
    assert tail.strip(), "tail must come after the marker"
    assert "characters elided" in marker
    # The exact count is pinned in test_tool_output; what matters here is that
    # the marker names a retrieval route for the part that was dropped.
    assert "last_output" in marker and "ref=" in marker


def test_result_stays_within_its_budget(engine):
    run = _long_run(passed=4000)
    shown = engine._tool_result_for_model("execute_shell", run)
    # 3_000 of payload plus the one-line marker.
    assert len(shown) <= 3_000 + 200


def test_document_read_is_still_exempt(engine):
    payload = "x" * 50_000
    assert engine._tool_result_for_model("document_read", payload) == payload


def test_skill_payload_keeps_its_larger_allowance(engine):
    skill = "y" * 20_000
    assert engine._tool_result_for_model("cli_anything_skill", skill) == skill


def test_an_ordinary_delegated_review_keeps_its_diffs_together(engine):
    payload = '{"status":"prepared","scope":{"reviewable_files":[' + (
        '"diff-line",' * 900) + ']},"rule_groups":[]}'

    assert 3_000 < len(payload) < 16_000
    assert engine._tool_result_for_model("review_code", payload) == payload


def test_a_large_review_still_gets_a_content_handle(engine):
    payload = "review-data-" * 2_000
    shown = engine._tool_result_for_model("review_code", payload)

    assert len(shown) < len(payload)
    assert "read_content with ref=review_code-" in shown
