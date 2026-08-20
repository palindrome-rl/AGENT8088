"""Regression coverage for the scope promised by ``/audit on``."""

import json


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


def test_approved_plan_mutations_still_use_the_plan_as_context(engine, tmp_path):
    seen = _enable_audit(engine, tmp_path)
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
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
