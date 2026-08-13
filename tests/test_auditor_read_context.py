"""The auditor must receive enough of a small generated file to verify it."""


def test_auditor_receives_complete_small_file_reads(engine, monkeypatch):
    content = "x" * 4_500
    monkeypatch.setattr(engine, "_active_role", "subagent:auditor")

    assert engine._tool_result_for_model("read_text", content) == content


def test_ordinary_tool_context_remains_bounded_and_marks_truncation(engine):
    content = "x" * 4_500

    result = engine._tool_result_for_model("read_text", content)

    assert len(result) < len(content)
    assert "truncated" in result
