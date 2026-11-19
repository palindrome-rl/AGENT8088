"""Compaction carries an earlier summary over as written instead of summarizing it again.

A summary of a summary loses exact paths, numbers and decisions, and costs another
model call over the whole history. The harness also appends what it knows exactly
(files written, verified checks) so those facts never depend on the summarizer, and
waits a few turns after a compaction before looking again.
"""
import json
from types import SimpleNamespace as N

import pytest


def _response(content):
    return N(choices=[N(message=N(content=content, tool_calls=None), finish_reason="stop")])


def _msgs(prefix, count):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{prefix} {i}"}
            for i in range(count)]


@pytest.fixture
def summarizer(engine, monkeypatch):
    """Records every prompt the summarizer receives; returns scripted summaries."""
    state = N(prompts=[], replies=["FIRST SUMMARY", "SECOND SUMMARY", "THIRD SUMMARY"], calls=0)

    def fake_completion(client, request, tools, **kwargs):
        state.prompts.append(request[0]["content"])
        reply = state.replies[min(state.calls, len(state.replies) - 1)]
        state.calls += 1
        return _response(reply)

    monkeypatch.setattr(engine, "create_completion", fake_completion)
    monkeypatch.setattr(engine, "_TURN_FILES_TOUCHED", [])
    monkeypatch.setattr(engine, "_VERDICT_LEDGER", {})
    return state


def _compact(engine, messages):
    assert engine.compact_messages(messages, keep=3, completion_client=object())


def test_the_first_compaction_is_unchanged(engine, summarizer):
    messages = _msgs("early", 8)
    recent = list(messages[-3:])
    _compact(engine, messages)
    assert messages == [{"role": "system", "content": "Conversation summary:\nFIRST SUMMARY"}, *recent]


def test_a_second_compaction_does_not_send_the_old_summary_to_the_summarizer(engine, summarizer):
    messages = _msgs("early", 8)
    _compact(engine, messages)
    messages.extend(_msgs("later", 8))
    _compact(engine, messages)
    assert "FIRST SUMMARY" not in summarizer.prompts[1]
    assert "later 0" in summarizer.prompts[1]
    assert "already exists and is kept as it is" in summarizer.prompts[1]


def test_the_old_summary_is_kept_word_for_word_ahead_of_the_new_one(engine, summarizer):
    messages = _msgs("early", 8)
    _compact(engine, messages)
    messages.extend(_msgs("later", 8))
    _compact(engine, messages)
    content = messages[0]["content"]
    assert content.startswith("Conversation summary:\nFIRST SUMMARY")
    assert content.index("FIRST SUMMARY") < content.index("SECOND SUMMARY")
    assert messages[0]["role"] == "system" and [m["role"] for m in messages].count("system") == 1


def test_a_third_compaction_keeps_both_earlier_sections(engine, summarizer):
    messages = _msgs("a", 8)
    _compact(engine, messages)
    for prefix in ("b", "c"):
        messages.extend(_msgs(prefix, 8))
        _compact(engine, messages)
    content = messages[0]["content"]
    assert [content.index(s) for s in ("FIRST SUMMARY", "SECOND SUMMARY", "THIRD SUMMARY")] == sorted(
        content.index(s) for s in ("FIRST SUMMARY", "SECOND SUMMARY", "THIRD SUMMARY"))


def test_with_nothing_new_to_summarize_nothing_happens(engine, summarizer):
    messages = [{"role": "system", "content": "Conversation summary:\nONLY SUMMARY"}, *_msgs("recent", 3)]
    assert not engine.compact_messages(messages, keep=3, completion_client=object())
    assert summarizer.calls == 0
    assert messages[0]["content"] == "Conversation summary:\nONLY SUMMARY"


def test_a_single_task_instruction_is_still_pinned(engine, summarizer):
    task = {"role": "user", "content": "Build /app/solution.py exactly as specified"}
    messages = [task, *[{"role": "assistant", "content": f"step {i}"} for i in range(9)]]
    _compact(engine, messages)
    assert messages[1] is task
    messages.extend([{"role": "assistant", "content": f"more {i}"} for i in range(6)])
    _compact(engine, messages)
    assert task in messages and messages.count(task) == 1


# --- what the harness knows exactly --------------------------------------------------------

def test_the_summary_ends_with_the_files_written_and_the_verified_checks(engine, summarizer, monkeypatch):
    monkeypatch.setattr(engine, "_TURN_FILES_TOUCHED", ["/w/app.py", "/w/data.json", "/w/app.py"])
    monkeypatch.setattr(engine, "verdict_ledger_summary",
                        lambda limit=10: "Verdicts already established this session:\n  PASSED: pytest -q")
    messages = _msgs("early", 8)
    _compact(engine, messages)
    content = messages[0]["content"]
    assert "## Harness record" in content
    assert "Files written or changed this run: /w/app.py, /w/data.json" in content
    assert "PASSED: pytest -q" in content
    assert content.index("FIRST SUMMARY") < content.index("## Harness record")


def test_the_record_is_rebuilt_not_stacked_on_the_next_compaction(engine, summarizer, monkeypatch):
    files = ["/w/one.py"]
    monkeypatch.setattr(engine, "_TURN_FILES_TOUCHED", files)
    messages = _msgs("a", 8)
    _compact(engine, messages)
    files.append("/w/two.py")
    messages.extend(_msgs("b", 8))
    _compact(engine, messages)
    content = messages[0]["content"]
    assert content.count("## Harness record") == 1
    assert "/w/one.py, /w/two.py" in content


def test_no_record_when_there_is_nothing_to_record(engine, summarizer):
    messages = _msgs("early", 8)
    _compact(engine, messages)
    assert "Harness record" not in messages[0]["content"]


def test_the_record_is_not_fed_to_the_summarizer_next_time(engine, summarizer, monkeypatch):
    monkeypatch.setattr(engine, "_TURN_FILES_TOUCHED", ["/w/secret_name_xyz.py"])
    messages = _msgs("a", 8)
    _compact(engine, messages)
    messages.extend(_msgs("b", 8))
    _compact(engine, messages)
    assert "secret_name_xyz" not in summarizer.prompts[1]


# --- keeping it bounded -------------------------------------------------------------------------

def test_the_oldest_sections_are_dropped_whole_never_rephrased(engine, summarizer, monkeypatch):
    monkeypatch.setattr(engine, "COMPACTION_SUMMARY_MAX_CHARS", 300)
    summarizer.replies[:] = ["A" * 120 + " alpha", "B" * 120 + " beta", "C" * 120 + " gamma"]
    messages = _msgs("a", 8)
    _compact(engine, messages)
    for prefix in ("b", "c"):
        messages.extend(_msgs(prefix, 8))
        _compact(engine, messages)
    content = messages[0]["content"]
    assert "gamma" in content and "alpha" not in content
    assert "earlier summary section(s) dropped to fit" in content
    assert "B" * 120 + " beta" in content or "beta" not in content, "a kept section is whole"


def test_the_dropped_count_survives_the_next_compaction(engine, summarizer, monkeypatch):
    monkeypatch.setattr(engine, "COMPACTION_SUMMARY_MAX_CHARS", 300)
    summarizer.replies[:] = ["A" * 150, "B" * 150, "C" * 150, "D" * 150]
    messages = _msgs("a", 8)
    _compact(engine, messages)
    for prefix in ("b", "c", "d"):
        messages.extend(_msgs(prefix, 8))
        _compact(engine, messages)
    first_line = messages[0]["content"].splitlines()[1]
    assert first_line.startswith("[") and first_line.endswith("dropped to fit]")
    assert int(first_line[1:].split()[0]) >= 2


def test_one_oversized_section_is_kept_rather_than_cut(engine, summarizer, monkeypatch):
    monkeypatch.setattr(engine, "COMPACTION_SUMMARY_MAX_CHARS", 2000)
    summarizer.replies[:] = ["X" * 5000]
    messages = _msgs("a", 8)
    _compact(engine, messages)
    assert "X" * 5000 in messages[0]["content"]


# --- the cooldown ---------------------------------------------------------------------------------

def _turns_that_compact(engine, monkeypatch, *, estimate, window=100, cooldown=6, turns=14):
    compactions = []
    monkeypatch.setattr(engine, "COMPACTION_THRESHOLD_PCT", 75)
    monkeypatch.setattr(engine, "COMPACTION_COOLDOWN_TURNS", cooldown)
    monkeypatch.setattr(engine, "_active_model_token_limits", lambda *a, **k: (window, 20))
    monkeypatch.setattr(engine, "_estimate_context_chars", lambda *a: estimate * 4)
    monkeypatch.setattr(engine, "_estimate_tokens", lambda chars: estimate)
    n = [0]

    def fake_compact(active, **kwargs):
        compactions.append(n[0])
        return True

    def fake_model(messages, tools, **kwargs):
        n[0] += 1
        if n[0] > turns:
            return _response("done")
        return _response(f'✿FUNCTION✿: execute_shell ✿ARGS✿: {json.dumps({"command": f"echo {n[0]}"})}')

    monkeypatch.setattr(engine, "compact_messages", fake_compact)
    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake_model)
    monkeypatch.setattr(engine, "exec_tool", lambda name, arguments, depth=0: "ok")
    messages = [{"role": "user", "content": "x"} for _ in range(8)]
    engine.run_agent(messages, max_turns=turns + 2, tools_def=[], allowed_tools={"execute_shell"},
                     dynamic_turns=False)
    return compactions


def test_a_compaction_waits_a_few_turns_before_the_next(engine, monkeypatch):
    compactions = _turns_that_compact(engine, monkeypatch, estimate=80)
    gaps = [b - a for a, b in zip(compactions, compactions[1:])]
    assert len(compactions) >= 2 and all(gap >= 6 for gap in gaps), compactions


def test_without_the_cooldown_it_looks_every_turn(engine, monkeypatch):
    compactions = _turns_that_compact(engine, monkeypatch, estimate=80, cooldown=0)
    assert len(compactions) > 8


def test_a_nearly_full_window_overrides_the_cooldown(engine, monkeypatch):
    compactions = _turns_that_compact(engine, monkeypatch, estimate=95)
    assert len(compactions) > 8


def test_the_summary_is_not_shown_to_the_model_twice(engine):
    """The summary lives in one system message; messages with a user role are untouched."""
    messages = [{"role": "system", "content": "Conversation summary:\nS1"},
                {"role": "user", "content": "Conversation summary: pasted by the user"}, *_msgs("r", 3)]
    sections, dropped, others = engine._split_prior_summary(messages)
    assert sections == ["S1"] and dropped == 0
    assert any("pasted by the user" in m["content"] for m in others)
