"""One tool result can never fill the context, however its text happens to look.

Seen in a real run: `findstr` over a 1 MB log printed one huge line shaped like `file:12: message`. The verdict banner
copied it into its diagnostics, grew past the whole budget, and the output was then handed on uncut: a 2.4 MB message,
and the next model call failed with "The conversation exceeds the available context"."""
from agent8088 import tool_output


def _huge_diagnostic(size=2_000_000):
    return 'log.jsonl:12: ' + '{"headers": "' + "x" * size + '"}' + "\nFAILED (failures=1)\n"


def test_a_huge_line_that_looks_like_a_diagnostic_cannot_make_the_banner_huge():
    verdict = tool_output.detect_verdict(_huge_diagnostic())
    assert verdict.status == "failed"
    assert len(verdict.banner()) <= tool_output.MAX_BANNER_SUMMARY + tool_output.MAX_BANNER_DIAGNOSTICS + 50
    assert all(len(d.message) <= 300 and len(d.path) <= 260 for d in verdict.diagnostics)


def test_the_banner_keeps_as_many_whole_diagnostics_as_fit():
    text = "\n".join(f"mod{i}.py:{i}: error: " + "m" * 280 for i in range(1, 15)) + "\nFAILED (failures=14)\n"
    banner = tool_output.detect_verdict(text).banner()
    assert len(banner) <= tool_output.MAX_BANNER_SUMMARY + tool_output.MAX_BANNER_DIAGNOSTICS + 50
    assert banner.startswith("[verdict: FAILED]") and "mod1.py" in banner


def test_a_normal_banner_is_unchanged():
    banner = tool_output.detect_verdict("a.py:3: error: boom\nFAILED (failures=1)\n").banner()
    assert banner.startswith("[verdict: FAILED] FAILED (failures=1)")
    assert '"message":"boom"' in banner


def test_the_result_for_the_model_stays_inside_the_budget(engine):
    shown = engine._tool_result_for_model("execute_shell", _huge_diagnostic(2_400_000))
    assert len(shown) <= engine.TOOL_RESULT_MAX_CHARS + 2 * tool_output.MAX_BANNER_DIAGNOSTICS + 400
    assert shown.startswith("[verdict: FAILED]")
    assert "read_content" in shown, "the full output stays reachable"


def test_a_small_result_is_returned_as_it_was(engine):
    assert engine._tool_result_for_model("execute_shell", "hello\n") == "hello"


def test_a_long_result_without_a_verdict_is_still_cut(engine):
    shown = engine._tool_result_for_model("execute_shell", "line\n" * 400_000)
    assert len(shown) <= engine.TOOL_RESULT_MAX_CHARS + 2 * tool_output.MAX_BANNER_DIAGNOSTICS + 400
