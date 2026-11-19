"""The elided middle stays reachable: nothing is actually thrown away.

Head-and-tail is a good guess about where the answer is. It is still a guess, so
on the rare turn the model needs the part that was cut, it must be able to ask
for it rather than re-run the command.
"""


def _clamped(engine, tool="execute_shell"):
    run = "START" + "\n".join(f"line {i} of noise" for i in range(4_000)) + "END"
    shown = engine._tool_result_for_model(tool, run)
    ref = shown.partition("ref=")[2].split()[0]
    return run, shown, ref


def test_a_clamped_result_advertises_a_usable_reference(engine):
    _, shown, ref = _clamped(engine)
    assert "[tool result truncated:" in shown
    assert ref and engine._OUTPUT_STORE.get(ref) is not None


def test_the_middle_can_be_fetched_back_by_reference(engine):
    run, _, ref = _clamped(engine)
    got = engine.run_tool("last_output", {"ref": ref, "offset": 5_000, "length": 300})
    assert run[5_000:5_300] in got


def test_content_handle_uses_the_same_store_without_changing_last_output(engine):
    run, _, ref = _clamped(engine)
    got = engine.run_tool("read_content", {"ref": ref, "offset": 5_000, "length": 300})
    assert run[5_000:5_300] in got
    assert "offset=5300" in got


def test_the_window_says_where_it_sits_and_what_comes_next(engine):
    _, _, ref = _clamped(engine)
    got = engine.run_tool("last_output", {"ref": ref, "offset": 1_000, "length": 200})
    assert "1000" in got          # where this window starts
    assert "offset=1200" in got   # how to continue


def test_a_stale_reference_is_answered_not_raised(engine):
    got = engine.run_tool("last_output", {"ref": "execute_shell-deadbeef"})
    assert "deadbeef" in got or "no stored output" in got.lower()


def test_last_output_with_no_reference_still_returns_the_last_result(engine):
    engine._last_tool_output, engine._last_tool_name = "40 passed in 1s", "execute_shell"
    got = engine.run_tool("last_output", {})
    assert "40 passed" in got


def test_a_fetched_window_fits_the_model_budget(engine):
    _, _, ref = _clamped(engine)
    got = engine.run_tool("last_output", {"ref": ref})
    shown = engine._tool_result_for_model("last_output", got)
    assert "[tool result truncated:" not in shown, (
        "the default window must fit, or retrieval re-creates the bug")
