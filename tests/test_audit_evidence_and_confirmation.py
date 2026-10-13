"""Issues 13 and 15: a test run is evidence, not something to audit; and one
small-model "fail" must not revert correct work on its own."""
import pytest


def _audit_spy(engine, monkeypatch, verdicts):
    """Record each auditor run and answer it from `verdicts`, in order."""
    runs = []

    def fake_subagent(args, depth=0, **kwargs):
        runs.append(args.get("task", ""))
        return f"[subagent:auditor] VERDICT: {verdicts[min(len(runs) - 1, len(verdicts) - 1)]}"

    monkeypatch.setattr(engine, "_exec_subagent", fake_subagent)
    return runs


# --- issue 13: run_tests and real test commands are not audited -------------------

@pytest.mark.parametrize("name,args,result", [
    ("run_tests", {}, "3 passed in 0.1s"),
    ("run_tests", {}, "1 failed, 2 passed in 0.1s"),
    ("execute_shell", {"command": "pytest -q"}, "1 failed, 2 passed in 0.1s"),
    ("execute_shell", {"command": "npm test"}, "5 passing"),
])
def test_a_test_run_is_evidence_and_is_not_audited(engine, name, args, result):
    assert engine._audit_is_redundant(name, args, result) is True


@pytest.mark.parametrize("name,args,result", [
    # No verdict in the output: nothing was established, so the audit stands.
    ("run_tests", {}, "could not collect any tests"),
    ("execute_shell", {"command": "pytest -q"}, "usage: pytest [options]"),
    # Not a test run at all.
    ("write_file", {"filename": "a.py", "content": "x"}, "wrote a.py"),
    ("execute_shell", {"command": "python build.py"}, "3 passed"),
    ("execute_shell", {"command": 'echo "3 passed"'}, "3 passed"),
])
def test_everything_else_is_still_audited(engine, name, args, result):
    assert engine._audit_is_redundant(name, args, result) is False


def test_acceptance_criteria_keep_a_test_step_audited(engine):
    assert engine._audit_is_redundant(
        "run_tests", {}, "3 passed", acceptance="every new branch is covered") is False


# --- issue 15: a fail is confirmed before anything is reverted --------------------

def test_a_single_fail_no_longer_reverts_on_its_own(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PLAN_AUDIT_CONFIRM_FAIL", True)
    runs = _audit_spy(engine, monkeypatch, ["fail", "pass"])
    target = tmp_path / "keep.txt"
    target.write_text("new content")
    monkeypatch.setattr(engine, "resolve_write_path", lambda name: target)

    out = engine._audit_tool_call("edit_file", {"filename": "keep.txt"}, "edited keep.txt",
                                  0, (target, b"old content"))

    assert len(runs) == 2, "a fail must be confirmed by a second, independent sample"
    assert target.read_text() == "new content", "correct work was reverted on one fail"
    assert "auditors disagreed" in out
    assert engine._take_audit_verdict() == "unknown"


def test_two_fails_still_revert(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PLAN_AUDIT_CONFIRM_FAIL", True)
    monkeypatch.setattr(engine, "PLAN_AUDIT_REVERT", True)
    runs = _audit_spy(engine, monkeypatch, ["fail", "fail"])
    target = tmp_path / "gone.txt"
    target.write_text("bad content")
    monkeypatch.setattr(engine, "resolve_write_path", lambda name: target)

    out = engine._audit_tool_call("edit_file", {"filename": "gone.txt"}, "edited gone.txt",
                                  0, (target, b"old content"))

    assert len(runs) == 2
    assert target.read_bytes() == b"old content", "a confirmed fail must revert"
    assert "failed verification" in out
    assert engine._take_audit_verdict() == "failed"


def test_a_pass_costs_only_one_auditor_run(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PLAN_AUDIT_CONFIRM_FAIL", True)
    runs = _audit_spy(engine, monkeypatch, ["pass"])
    target = tmp_path / "ok.txt"
    target.write_text("content")
    monkeypatch.setattr(engine, "resolve_write_path", lambda name: target)

    engine._audit_tool_call("edit_file", {"filename": "ok.txt"}, "edited ok.txt", 0, None)

    assert len(runs) == 1


def test_confirmation_can_be_switched_off(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PLAN_AUDIT_CONFIRM_FAIL", False)
    monkeypatch.setattr(engine, "PLAN_AUDIT_REVERT", True)
    runs = _audit_spy(engine, monkeypatch, ["fail", "pass"])
    target = tmp_path / "gone.txt"
    target.write_text("bad content")
    monkeypatch.setattr(engine, "resolve_write_path", lambda name: target)

    engine._audit_tool_call("edit_file", {"filename": "gone.txt"}, "edited gone.txt",
                            0, (target, b"old content"))

    assert len(runs) == 1
    assert target.read_bytes() == b"old content"


# --- issue 13, wired into the tool path -------------------------------------------

import json


@pytest.mark.parametrize("name,args,output,audited", [
    ("run_tests", {}, "3 passed in 0.1s", False),
    # A failing test run is evidence too: the failure is already the finding.
    ("run_tests", {}, "1 failed, 2 passed in 0.1s", False),
    ("execute_shell", {"command": "python -m pytest -q"}, "1 failed", False),
    # No verdict means nothing was established.
    ("run_tests", {}, "could not collect any tests", True),
])
def test_test_runs_do_not_spawn_an_auditor_through_exec_tool(
        engine, monkeypatch, name, args, output, audited):
    monkeypatch.setattr(engine, "PLAN_AUDIT", True)
    monkeypatch.setattr(engine, "PLAN_AUDIT_REVERT", False)
    monkeypatch.setattr(engine, "run_tool", lambda *a, **k: output)
    monkeypatch.setattr(engine, "_audit_applies", lambda *a, **k: True)
    seen = []
    monkeypatch.setattr(engine, "_audit_tool_call", lambda *a: seen.append(a) or output)
    engine.exec_tool(name, json.dumps(args))
    assert bool(seen) == audited
