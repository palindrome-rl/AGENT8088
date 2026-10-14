"""Regression coverage for the scope promised by ``/audit on``."""

import json

import pytest


@pytest.mark.parametrize("command", [None, 123, {"unexpected": "object"}, []])
def test_malformed_shell_command_remains_a_tool_error(engine, monkeypatch, command):
    monkeypatch.setattr(engine, "run_tool", lambda *a, **k: "Error: command must be a string")
    assert not engine._standalone_test_command(command)
    result = engine.exec_tool("execute_shell", json.dumps({"command": command}))
    assert "Error: command must be a string" in result


@pytest.mark.parametrize("command,output,audited", [
    ("python -m pytest -q", "3 passed", False),
    ("python verify.py", "1 passed", True),
    ("python fix.py && python -m pytest -q", "3 passed", True),
    ("python -m pytest -q", "no verdict available", True),
])
def test_passing_tests_do_not_spawn_a_redundant_auditor(
        engine, monkeypatch, command, output, audited):
    monkeypatch.setattr(engine, "PLAN_AUDIT", True)
    monkeypatch.setattr(engine, "PLAN_AUDIT_REVERT", False)
    monkeypatch.setattr(engine, "run_tool", lambda *a, **k: output)
    seen = []
    monkeypatch.setattr(engine, "_audit_tool_call",
                        lambda *a: seen.append(a) or output)
    engine.exec_tool("execute_shell", json.dumps({"command": command}))
    assert bool(seen) == audited


def _enable_audit(engine, tmp_path, verdict="VERDICT: pass"):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PLAN_AUDIT = True
    engine.PLAN_AUDIT_REVERT = True
    engine.PERMISSION_MODE = "full-auto"
    seen = []
    engine._exec_subagent = lambda args, depth=0: seen.append(args) or verdict
    return seen


def _write(engine, path, content="new", *, depth=0):
    return engine.exec_tool(
        "write_file",
        json.dumps({"filename": str(path), "content": content}),
        depth=depth,
    )


def test_audit_on_verifies_an_ordinary_top_level_mutation(engine, tmp_path):
    seen = _enable_audit(engine, tmp_path)

    _write(engine, tmp_path / "ordinary.txt")

    assert [call["agent_type"] for call in seen] == ["auditor"]
    assert "Approved plan context" not in seen[0]["task"]


def test_audit_off_leaves_ordinary_work_unverified(engine, tmp_path):
    seen = _enable_audit(engine, tmp_path)
    engine.PLAN_AUDIT = False

    _write(engine, tmp_path / "off.txt")

    assert seen == []


def test_subagent_mutations_are_not_recursively_audited(engine, tmp_path):
    seen = _enable_audit(engine, tmp_path)

    _write(engine, tmp_path / "nested.txt", depth=1)

    assert seen == []


def test_approved_plan_mutations_still_use_the_plan_as_context(engine, tmp_path, monkeypatch):
    seen = _enable_audit(engine, tmp_path)
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
    # present_plan writes PLAN.md to the project root; keep that out of the repo.
    with monkeypatch.context() as scoped:
        scoped.setattr(engine, "PROJECT_ROOT", tmp_path)
        engine.run_tool("present_plan", {"plan": "write planned.txt"})

    _write(engine, tmp_path / "planned.txt")

    assert [call["agent_type"] for call in seen] == ["auditor"]
    assert "Approved plan context" in seen[0]["task"]
    assert "write planned.txt" in seen[0]["task"]


def test_failed_ordinary_audit_restores_previous_contents(engine, tmp_path):
    _enable_audit(engine, tmp_path, "VERDICT: fail — contents are incorrect")
    target = tmp_path / "restore.txt"
    target.write_text("original", encoding="utf-8")

    result = _write(engine, target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert "verification failed" in result
    assert "Stop the current work" in result
    assert "carrying out the plan" not in result


def test_blocked_mutation_is_not_audited_before_it_runs(engine, tmp_path):
    seen = _enable_audit(engine, tmp_path)
    engine.PERMISSION_MODE = "readonly"
    target = tmp_path / "blocked.txt"

    result = _write(engine, target)

    assert result.startswith("ESCALATION_REQUEST\x1f")
    assert seen == []
    assert not target.exists()


def test_non_mutating_calls_are_not_audited(engine, tmp_path):
    seen = _enable_audit(engine, tmp_path)

    result = engine.exec_tool("calculate", json.dumps({"expression": "2 + 2"}))

    assert "4" in result
    assert seen == []


def test_a_passing_audit_says_so(engine, monkeypatch):
    """A silent pass is indistinguishable from no audit at all.

    The auditor ran, read the file, confirmed it -- and returned ("", ""), so
    nothing reached the tool result. The turn then reported "Changed work was
    inspected, but no automated verification passed" directly underneath a
    verification that had passed.
    """
    monkeypatch.setattr(engine, "PLAN_AUDIT", True)
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda args, depth=0: "Checked the file.\nVERDICT: pass")

    reason, note = engine._audit_plan_step("write_file x", "write_file", {}, "ok", 0)

    assert reason == "", "a pass must not halt anything"
    assert engine.AUDIT_PASSED_NOTE in note, "the pass has to be visible"


def test_an_inconclusive_audit_is_not_reported_as_a_pass(engine, monkeypatch):
    monkeypatch.setattr(engine, "PLAN_AUDIT", True)
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda args, depth=0: "Could not find it.\nVERDICT: unknown")

    reason, note = engine._audit_plan_step("write_file x", "write_file", {}, "ok", 0)

    assert reason == "", "an auditor that could not look must not halt work"
    assert engine.AUDIT_UNKNOWN_NOTE in note
    assert engine.AUDIT_PASSED_NOTE not in note


def test_a_failing_audit_still_halts(engine, monkeypatch):
    """The fix must not make every verdict look like a pass."""
    monkeypatch.setattr(engine, "PLAN_AUDIT", True)
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda args, depth=0: "File is empty.\nVERDICT: fail")

    reason, note = engine._audit_plan_step("write_file x", "write_file", {}, "ok", 0)

    assert "failed verification" in reason
    assert engine.AUDIT_PASSED_NOTE not in note


def test_a_passing_audit_counts_as_the_turn_s_verification():
    """detect_verdict reads test output and cannot recognise an auditor.

    Without the mapping the turn stayed on "inspected", which prints as "no
    automated verification passed" -- underneath a passed verification.
    """
    from agent8088.trajectory import TrajectoryState

    tracker = TrajectoryState({})
    op = tracker.before_tool("write_file")
    tracker.after_tool(op, "wrote 11 bytes", failed=False, blocked=False,
                       mutated=True, verdict="passed")

    assert "no automated verification passed" not in tracker.review()
    assert not tracker.needs_verification()


def test_without_a_verdict_the_turn_still_admits_it_is_unverified():
    """The fix must not silence the warning for work nothing checked."""
    from agent8088.trajectory import TrajectoryState

    tracker = TrajectoryState({})
    op = tracker.before_tool("write_file")
    tracker.after_tool(op, "wrote 11 bytes", failed=False, blocked=False,
                       mutated=True, verdict="unknown")

    assert tracker.needs_verification()


@pytest.fixture(autouse=True)
def _model_audit_runs(engine, monkeypatch):
    """These tests exercise the model auditor; the read-back shortcut for an
    ordinary write_file would answer first (see test_auditor_robustness)."""
    monkeypatch.setattr(engine, "_readback_matches", lambda *a, **k: False)
