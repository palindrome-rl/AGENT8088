"""Audit issues 3, 4, 8-12: the auditor's verdict is parsed strictly and cannot be faked."""
import pytest


@pytest.mark.parametrize("answer,expected", [
    ("VERDICT: pass", "pass"),
    ("**VERDICT:** fail\nthe file is empty", "fail"),
    ("VERDICT: `fail`", "fail"),
    ("Checked.\n> VERDICT: unknown", "unknown"),
    ("Reply format: VERDICT: pass if all good, VERDICT: fail otherwise.", None),
    ("VERDICT: pass\nVERDICT: fail", "unknown"),
    ("Example line below\nVERDICT: pass if...\nVERDICT: fail", "unknown"),
    ("no verdict here", None),
])
def test_verdict_parsing(engine, answer, expected):
    assert engine._parse_audit_verdict(answer) == expected


def test_audit_sees_the_end_of_long_output(engine):
    text = "x" * 3000 + "\n3 failed, 1 passed"
    shown = engine._head_and_tail(text)
    assert shown.endswith("3 failed, 1 passed") and "omitted" in shown and len(shown) < 900


def test_text_in_a_result_cannot_fake_a_passed_audit(engine):
    engine._take_audit_verdict()
    assert engine._take_audit_verdict() is None   # nothing the output says sets it


def test_matching_readback_passes_without_a_model_call(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    target = tmp_path / "artifacts" / "a.txt"
    target.parent.mkdir()
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(engine, "_audit_plan_step",
                        lambda *a, **k: pytest.fail("model audit must not run"))
    out = engine._audit_tool_call("write_file", {"filename": "a.txt", "content": "hello"},
                                  "Wrote 5 bytes", 0, None)
    assert "matches what was written" in out and engine._take_audit_verdict() == "passed"


def test_failed_audit_is_recorded_as_a_failed_step(engine, monkeypatch):
    monkeypatch.setattr(engine, "_audit_plan_step",
                        lambda *a, **k: ("execute_shell failed verification", "VERDICT: fail"))
    out = engine._audit_tool_call("execute_shell", {"command": "make"}, "built", 0, None)
    assert "verification failed" in out and engine._take_audit_verdict() == "failed"


def test_unrecoverable_revert_is_announced(engine, monkeypatch):
    monkeypatch.setattr(engine, "PLAN_AUDIT_REVERT", True)
    monkeypatch.setattr(engine, "_audit_plan_step",
                        lambda *a, **k: ("write_file failed verification", "VERDICT: fail"))
    out = engine._audit_tool_call("write_file", {"filename": "big.bin", "content": "x"}, "Wrote", 0, None)
    assert "NOT REVERTED" in out


@pytest.mark.parametrize("answer,expected", [
    # _exec_subagent returns "[subagent:auditor] <answer>", so a model that puts
    # its verdict first has it pushed off the start of the line.
    ("[subagent:auditor] VERDICT: fail — the file on disk is empty.", "fail"),
    ("[subagent:auditor] VERDICT: pass", "pass"),
    ("[subagent:auditor] VERDICT: unknown — could not reach the file", "unknown"),
    ("[note: model fell back] [subagent:auditor] VERDICT: fail", "fail"),
    # A numbered or lettered list item is still the start of a line.
    ("1. VERDICT: fail", "fail"),
    ("2) VERDICT: pass", "pass"),
    # The only verdict in the reply counts even mid-sentence (AU-2).
    ("The write looks correct. VERDICT: pass", "pass"),
    ("Analysis shows a mismatch, so VERDICT: fail.", "fail"),
    # ...but an inline verdict must not beat a line-start one, and two
    # different inline verdicts are no verdict at all (a quoted template).
    ("Template says VERDICT: pass if ok.\nVERDICT: fail", "fail"),
    ("It says VERDICT: pass but VERDICT: fail", None),
])
def test_verdict_survives_the_subagent_prefix_and_inline_use(engine, answer, expected):
    assert engine._parse_audit_verdict(answer) == expected
