"""Verification governs what persists, and what verification costs.

Three properties, in the order they matter:

1. A write that fails verification is put back — only verified state survives.
2. A step may declare what "done" means; the auditor grades against that rather
   than against a criterion it invented.
3. The cost of verification is attributed and reportable, because a cost you
   cannot see is a cost you cannot decide about.
"""
import json

from tests.conftest import ScriptedModel


def _plan(engine, steps):
    return engine._exec_plan({"steps": json.dumps(steps)})


def _auditor_says(engine, monkeypatch, verdict):
    seen = []
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda args, depth=0: seen.append(args) or verdict)
    return seen


def _enable(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    engine.PLAN_AUDIT = True
    engine.PLAN_AUDIT_REVERT = True


# ---------------------------------------------------------------------------
# 1. Only verified state persists
# ---------------------------------------------------------------------------

def test_failed_audit_removes_a_file_the_step_created(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    _auditor_says(engine, monkeypatch, "VERDICT: fail — wrong content")
    target = tmp_path / "new.txt"
    out = _plan(engine, [{"tool": "write_file",
                          "arguments": {"filename": str(target), "content": "bad"}}])
    assert not target.exists(), "an unverified creation must not survive"
    assert "reverted" in out


def test_failed_audit_restores_the_previous_contents(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    _auditor_says(engine, monkeypatch, "VERDICT: fail — clobbered the config")
    target = tmp_path / "existing.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    _plan(engine, [{"tool": "write_file",
                    "arguments": {"filename": str(target), "content": "REPLACED"}}])
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_passing_audit_keeps_the_write(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    _auditor_says(engine, monkeypatch, "VERDICT: pass — content matches")
    target = tmp_path / "kept.txt"
    _plan(engine, [{"tool": "write_file",
                    "arguments": {"filename": str(target), "content": "good"}}])
    assert target.read_text(encoding="utf-8") == "good"


def test_revert_can_be_switched_off(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    engine.PLAN_AUDIT_REVERT = False
    _auditor_says(engine, monkeypatch, "VERDICT: fail — wrong")
    target = tmp_path / "left.txt"
    out = _plan(engine, [{"tool": "write_file",
                          "arguments": {"filename": str(target), "content": "x"}}])
    assert target.exists(), "revert disabled means the file stays"
    assert "halted" in out


def test_oversized_file_is_not_reverted_and_says_so(engine, tmp_path, monkeypatch):
    """Declining to revert is fine; implying a revert that did not happen is not."""
    _enable(engine, tmp_path)
    engine.PLAN_REVERT_MAX_BYTES = 8
    _auditor_says(engine, monkeypatch, "VERDICT: fail — wrong")
    target = tmp_path / "big.txt"
    target.write_text("X" * 64, encoding="utf-8")
    out = _plan(engine, [{"tool": "write_file",
                          "arguments": {"filename": str(target), "content": "y"}}])
    assert target.read_text(encoding="utf-8") == "y", "too large to snapshot"
    assert "reverted" not in out


def test_a_shell_step_reports_that_it_cannot_be_undone(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    # The command must actually run: a step blocked at the permission gate halts
    # before it is ever audited, so it would never reach the no-undo branch.
    monkeypatch.setattr(engine, "_exec_shell_command", lambda *a, **k: "hi")
    _auditor_says(engine, monkeypatch, "VERDICT: fail — did nothing")
    out = _plan(engine, [{"tool": "execute_shell", "arguments": {"command": "echo hi"}}])
    assert "no undo" in out


def test_revert_touches_only_the_failed_step(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    verdicts = iter(["VERDICT: pass — fine", "VERDICT: fail — wrong"])
    monkeypatch.setattr(engine, "_exec_subagent", lambda args, depth=0: next(verdicts))
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    _plan(engine, [
        {"tool": "write_file", "arguments": {"filename": str(first), "content": "keep"}},
        {"tool": "write_file", "arguments": {"filename": str(second), "content": "drop"}},
    ])
    assert first.read_text(encoding="utf-8") == "keep", "a verified step must not be undone"
    assert not second.exists()


# ---------------------------------------------------------------------------
# 2. Acceptance criteria and evidence
# ---------------------------------------------------------------------------

def test_acceptance_criteria_reach_the_auditor(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    seen = _auditor_says(engine, monkeypatch, "VERDICT: pass — ok")
    _plan(engine, [{
        "tool": "write_file",
        "arguments": {"filename": str(tmp_path / "c.txt"), "content": "v2"},
        "acceptance": "the file contains exactly v2",
        "evidence": "cat the file",
    }])
    task = seen[0]["task"]
    assert "the file contains exactly v2" in task
    assert "cat the file" in task


def test_a_declared_criterion_makes_any_step_auditable(engine, tmp_path, monkeypatch):
    """browse_page has no closure of its own, but a stated criterion supplies one."""
    _enable(engine, tmp_path)
    seen = _auditor_says(engine, monkeypatch, "VERDICT: pass — ok")
    monkeypatch.setattr(engine, "_exec_browser", lambda args: "page text")
    _plan(engine, [{"tool": "browse_page",
                    "arguments": {"url": "https://example.com"},
                    "acceptance": "the page mentions pricing"}])
    assert len(seen) == 1


def test_browser_steps_are_not_audited_without_a_criterion(engine, tmp_path, monkeypatch):
    """A rendered page closes over nothing; auditing it just buys an unknown."""
    _enable(engine, tmp_path)
    seen = _auditor_says(engine, monkeypatch, "VERDICT: pass — ok")
    monkeypatch.setattr(engine, "_exec_browser", lambda args: "page text")
    _plan(engine, [{"tool": "browse_page", "arguments": {"url": "https://example.com"}}])
    assert seen == []


# ---------------------------------------------------------------------------
# 3. Verification cost is attributed
# ---------------------------------------------------------------------------

def test_subagent_tokens_are_attributed_to_their_role(engine, monkeypatch):
    engine._active_budget = engine._TurnBudget(max_tokens=0)
    monkeypatch.setattr(engine, "create_completion", ScriptedModel(["VERDICT: pass — ok"]))

    def _spend(*_a, **_k):
        engine._active_budget.add_tokens(100, 50)
        return "VERDICT: pass — ok"

    monkeypatch.setattr(engine, "run_agent", _spend)
    engine._active_budget.add_tokens(300, 200)          # main-loop spend
    engine._exec_subagent({"agent_type": "auditor", "task": "check"}, depth=0)

    assert engine._active_budget.role_total("main") == 500
    assert engine._active_budget.role_total("subagent:auditor") == 150
    assert abs(engine._active_budget.audit_share() - 150 / 650) < 1e-9


def test_role_is_restored_after_the_sub_run(engine, monkeypatch):
    monkeypatch.setattr(engine, "run_agent", lambda *_a, **_k: "ok")
    engine._exec_subagent({"agent_type": "auditor", "task": "check"}, depth=0)
    assert engine._active_role == "main"


def test_plan_reports_what_verification_cost(engine, tmp_path, monkeypatch):
    _enable(engine, tmp_path)
    engine._active_budget = engine._TurnBudget()
    engine._active_budget.add_tokens(900, 0)

    def _audit(args, depth=0):
        engine._active_role = "subagent:auditor"
        engine._active_budget.add_tokens(100, 0)
        engine._active_role = "main"
        return "VERDICT: pass — ok"

    monkeypatch.setattr(engine, "_exec_subagent", _audit)
    out = _plan(engine, [{"tool": "write_file",
                          "arguments": {"filename": str(tmp_path / "t.txt"),
                                        "content": "x"}}])
    assert "Verification cost this turn: 10% of tokens" in out


def test_telemetry_records_the_spending_role(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_ENABLED", True)
    monkeypatch.setattr(engine, "MODEL_TELEMETRY_PATH", tmp_path / "t.jsonl")
    engine._active_role = "subagent:auditor"
    engine._record_model_telemetry("p", "m", "primary", engine.time.monotonic(),
                                   max_tokens=10, response=None)
    engine._active_role = "main"
    entry = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert entry["role"] == "subagent:auditor"


# ---------------------------------------------------------------------------
# End to end, through run_agent
# ---------------------------------------------------------------------------

def test_end_to_end_unverified_write_does_not_survive_the_turn(engine, tmp_path, monkeypatch):
    """Model plans a write -> step runs -> auditor rejects it -> file is gone."""
    _enable(engine, tmp_path)
    target = tmp_path / "e2e.txt"
    plan = json.dumps([{
        "tool": "write_file",
        "arguments": {"filename": str(target), "content": "unverified"},
        "acceptance": "the file contains the approved release notes",
    }])

    # The main loop calls execute_plan, then answers. The auditor sub-run is a
    # separate scripted model that rejects the write.
    monkeypatch.setattr(engine, "_exec_subagent",
                        lambda args, depth=0: "VERDICT: fail — not the release notes")
    engine.create_completion = ScriptedModel([
        '✿FUNCTION✿: execute_plan ✿ARGS✿: ' + json.dumps({"steps": plan}),
        "I stopped: the write did not match the acceptance criteria.",
    ])

    answer = engine.run_agent([{"role": "user", "content": "write the release notes"}],
                              max_turns=4)
    assert not target.exists(), "the turn must not leave unverified state behind"
    assert "stopped" in answer.lower()
