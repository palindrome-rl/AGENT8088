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
