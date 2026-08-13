"""Verification has to reach the work, wherever the work happens.

The auditor used to reach a plan's writes for a structural reason rather than a
deliberate one: plan mode forced everything through `execute_plan`, and that was
the only path it hooked. Approved plans now run as ordinary tool calls, which
would have left the default `/plan` path as the one path with no verification at
all — the opposite of the intent.

Also pinned here, against two claims that had been made about the auditor. Its
browser scope is what the code always did and not what the claim said: a stated
criterion does make a browser step checkable, because the reported page text is in
the auditor's task. And a profile pinned to readonly is now *refused* the write
rather than offered an escalation the user could approve — sub-agent escalations do
reach the user, so "it only observes" was a question, and it should be a guarantee.
"""
import json

import pytest


def _audit_on(engine, tmp_path, verdict="VERDICT: pass"):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PLAN_AUDIT = True
    engine.PLAN_AUDIT_REVERT = True
    seen = []
    engine._exec_subagent = lambda args, depth=0: seen.append(args) or verdict
    return seen


def _approve(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
    engine.run_tool("present_plan", {"plan": "1. write out.txt"})


# ---------------------------------------------------------------------------
# The audit reaches post-approval tool calls
# ---------------------------------------------------------------------------

def test_a_post_approval_write_is_verified(engine, tmp_path):
    seen = _audit_on(engine, tmp_path)
    _approve(engine)
    target = tmp_path / "out.txt"

    engine.exec_tool("write_file", json.dumps({"filename": str(target), "content": "good"}))

    assert [s["agent_type"] for s in seen] == ["auditor"]
    assert target.read_text(encoding="utf-8") == "good", "a passing audit keeps the write"


def test_a_post_approval_write_that_fails_verification_is_put_back(engine, tmp_path):
    _audit_on(engine, tmp_path, "VERDICT: fail — contains 'bad', not a revenue table")
    _approve(engine)
    target = tmp_path / "out.txt"

    out = engine.exec_tool("write_file", json.dumps({"filename": str(target), "content": "bad"}))

    assert not target.exists(), "an unverified creation must not survive"
    assert "reverted" in out
    assert "verification failed" in out


def test_a_failed_verification_restores_the_previous_contents(engine, tmp_path):
    _audit_on(engine, tmp_path, "VERDICT: fail — clobbered it")
    _approve(engine)
    target = tmp_path / "out.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    engine.exec_tool("write_file", json.dumps({"filename": str(target), "content": "REPLACED"}))

    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_a_failed_verification_tells_the_model_to_stop(engine, tmp_path):
    """The halt in _exec_plan has no step list to stop here, so the equivalent is
    an unambiguous instruction not to build on top of an unverified step."""
    _audit_on(engine, tmp_path, "VERDICT: fail — wrong")
    _approve(engine)

    out = engine.exec_tool("write_file",
                           json.dumps({"filename": str(tmp_path / "out.txt"), "content": "x"}))

    assert "Stop" in out and "later step" in out


def test_nothing_is_audited_before_a_plan_is_approved(engine, tmp_path):
    seen = _audit_on(engine, tmp_path)
    engine.PERMISSION_MODE = "full-auto"

    engine.exec_tool("write_file",
                     json.dumps({"filename": str(tmp_path / "free.txt"), "content": "x"}))

    assert seen == [], "ordinary work outside a plan is not the auditor's business"


def test_a_subagents_writes_are_not_audited(engine, tmp_path):
    """Auditing at depth would have the auditor verify itself, forever."""
    seen = _audit_on(engine, tmp_path)
    _approve(engine)

    engine.exec_tool("write_file",
                     json.dumps({"filename": str(tmp_path / "sub.txt"), "content": "x"}),
                     depth=1)

    assert seen == []


def test_a_blocked_call_is_not_audited(engine, tmp_path):
    """Nothing happened yet — it will be audited on the retry after approval.

    The prefix is \x1f-delimited. This assertion read ':' and so passed for the
    wrong reason once the wire format changed: `out` was not recognised as an
    escalation, the audit ran, and the test's own `seen == []` then failed.
    """
    seen = _audit_on(engine, tmp_path)
    _approve(engine)
    engine.set_permission_mode("readonly")
    engine._plan_approved = True

    target = tmp_path / "gated.txt"
    out = engine.exec_tool("write_file",
                           json.dumps({"filename": str(target), "content": "x"}))

    assert out.startswith("ESCALATION_REQUEST\x1f")
    assert seen == [], "a call that was blocked has nothing to verify yet"
    assert not target.exists(), "the write was gated, so nothing should be on disk"
    # The escalation is what the user has to answer. Auditing a write that never
    # happened appends a fail verdict, a claim to have reverted a file that was
    # never created, and an instruction to abandon the plan — onto the very
    # payload the approval prompt renders.
    assert "audit:" not in out
    assert "reverted" not in out
    assert "verification failed" not in out


def test_an_inconclusive_audit_does_not_undo_the_write(engine, tmp_path):
    """Same asymmetry as in plans: a verifier that cannot reach its model must not
    be able to destroy work on its own."""
    _audit_on(engine, tmp_path, "the model is unreachable")
    _approve(engine)
    target = tmp_path / "out.txt"

    out = engine.exec_tool("write_file", json.dumps({"filename": str(target), "content": "x"}))

    assert target.exists()
    assert "inconclusive" in out
    assert "verification failed" not in out


def test_audit_is_off_unless_plan_audit_is_set(engine, tmp_path):
    seen = _audit_on(engine, tmp_path)
    engine.PLAN_AUDIT = False
    _approve(engine)

    engine.exec_tool("write_file",
                     json.dumps({"filename": str(tmp_path / "out.txt"), "content": "x"}))

    assert seen == []


def test_reads_are_not_audited(engine, tmp_path):
    seen = _audit_on(engine, tmp_path)
    _approve(engine)
    (tmp_path / "r.txt").write_text("hi", encoding="utf-8")

    engine.exec_tool("read_text", json.dumps({"filename": str(tmp_path / "r.txt")}))

    assert seen == [], "a read leaves nothing to verify"


def test_an_approved_plan_tells_the_model_its_steps_will_be_checked(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PLAN_AUDIT = True
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"

    out = engine.run_tool("present_plan", {"plan": "1. write out.txt"})

    assert "verified" in out.lower()


# ---------------------------------------------------------------------------
# Browser steps stay out of scope, acceptance criteria or not
# ---------------------------------------------------------------------------

def test_browser_steps_need_a_stated_criterion_to_be_auditable(engine):
    """Pinning what is actually true, against a description that said browser steps
    were removed from scope entirely. They are not: a rendered page closes over
    nothing of its own, but the reported page text reaches the auditor's task, so a
    criterion the plan states is checkable. What is excluded is auditing a page on
    a criterion the auditor had to invent."""
    assert engine._plan_step_is_auditable("browse_page", "") is False
    assert engine._plan_step_is_auditable("browse_page", "the page shows a login form") is True


def test_durable_steps_stay_auditable(engine):
    assert engine._plan_step_is_auditable("write_file", "") is True
    assert engine._plan_step_is_auditable("execute_shell", "") is True
    assert engine._plan_step_is_auditable("read_text", "") is False
    assert engine._plan_step_is_auditable("read_text", "the file says hi") is True


# ---------------------------------------------------------------------------
# A readonly-pinned profile is refused, not asked
# ---------------------------------------------------------------------------

def test_a_readonly_pinned_agent_is_refused_the_write(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    inside = {}

    def spy(messages, **kw):
        inside["result"] = engine.run_tool(
            "write_file", {"filename": str(tmp_path / "audited.txt"), "content": "x"},
            depth=1)
        return "VERDICT: pass"

    monkeypatch.setattr(engine, "run_agent", spy)
    engine._exec_subagent({"agent_type": "auditor", "task": "check it"})

    # \x1f, not ':'. As a *negative* assertion against the old prefix this passed
    # for free once the wire format changed — it would no longer have noticed the
    # pin regressing and the auditor being offered an escalation again.
    assert not inside["result"].startswith("ESCALATION_REQUEST\x1f"), (
        "an escalation is a question the user could answer yes to — "
        "'it only observes' has to be a refusal")
    assert "read-only" in inside["result"] or "readonly" in inside["result"]
    assert not (tmp_path / "audited.txt").exists()


def test_the_pin_is_lifted_when_the_subagent_finishes(engine, tmp_path, monkeypatch):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(engine, "run_agent", lambda messages, **kw: "VERDICT: pass")

    engine._exec_subagent({"agent_type": "auditor", "task": "check it"})

    out = engine.run_tool("write_file", {"filename": str(tmp_path / "after.txt"), "content": "x"})
    assert (tmp_path / "after.txt").exists(), f"parent still pinned: {out[:120]}"


def test_a_normal_readonly_turn_still_escalates(engine, tmp_path):
    """Only a *pinned* profile is refused outright. In plain readonly mode a write
    must still be able to ask the user, which is the whole approval flow."""
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "readonly"

    out = engine.run_tool("write_file", {"filename": str(tmp_path / "ask.txt"), "content": "x"})

    assert out.startswith("ESCALATION_REQUEST\x1f")


@pytest.mark.parametrize("profile", ["explore", "researcher", "general-purpose", "coder"])
def test_profiles_without_a_declared_floor_are_unaffected(engine, tmp_path, monkeypatch, profile):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    inside = {}

    def spy(messages, **kw):
        inside["mode"] = engine.PERMISSION_MODE
        return "done"

    monkeypatch.setattr(engine, "run_agent", spy)
    engine._exec_subagent({"agent_type": profile, "task": "do it"})

    assert inside["mode"] == "full-auto"


# ---------------------------------------------------------------------------
# Verification's cost stays visible on the new path too
# ---------------------------------------------------------------------------

def _spend(engine, budget, main, audited):
    """Bill tokens the way the loop does — role comes from the active-role global."""
    engine._active_role = "main"
    budget.add_tokens(0, main)
    engine._active_role = "subagent:auditor"
    budget.add_tokens(0, audited)
    engine._active_role = "main"
    return "done"


def test_the_turns_verification_share_survives_the_budget(engine, monkeypatch):
    """_exec_plan appended a cost line from the live budget. The post-approval path
    has no such line, so the share is captured as the turn's budget is torn down —
    otherwise the one flow that spends audit tokens by default reports nothing."""
    monkeypatch.setattr(engine, "_run_agent_loop",
                        lambda messages, budget=None, **kw: _spend(engine, budget, 900, 100))

    engine.run_agent([{"role": "user", "content": "go"}])

    assert abs(engine.last_audit_share() - 0.1) < 1e-9


def test_a_turn_with_no_verification_reports_no_share(engine, monkeypatch):
    monkeypatch.setattr(engine, "_run_agent_loop",
                        lambda messages, budget=None, **kw: _spend(engine, budget, 500, 0))

    engine.run_agent([{"role": "user", "content": "go"}])

    assert engine.last_audit_share() == 0.0


# ---------------------------------------------------------------------------
# The auditor grades against the plan the user approved
# ---------------------------------------------------------------------------

def test_the_auditor_treats_the_plan_as_context_not_a_per_call_criterion(engine, tmp_path):
    """Later plan steps must not make an earlier successful call fail verification."""
    seen = _audit_on(engine, tmp_path)
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
    plan = "## Goal\nrevenue.txt holds a markdown table with at least four rows"
    engine.run_tool("present_plan", {"plan": plan})

    engine.exec_tool("write_file",
                     json.dumps({"filename": str(tmp_path / "revenue.txt"), "content": "hi"}))

    assert seen, "the auditor should have been asked"
    task = seen[0]["task"]
    assert "at least four rows" in task, "the approved plan remains useful context"
    assert "Acceptance criteria" not in task
    assert "do not require later steps" in task


def test_auditor_may_run_code_only_inside_a_readonly_sandbox(engine, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: "native")
    seen = {}

    def spy(messages, **kw):
        seen["allowed"] = engine.check_permission("shell", "python library.py")
        seen["readonly"] = engine._sandbox_readonly
        return "VERDICT: pass"

    monkeypatch.setattr(engine, "run_agent", spy)
    engine._exec_subagent({"agent_type": "auditor", "task": "run it"})

    assert seen == {"allowed": True, "readonly": True}


def test_the_approved_plan_is_forgotten_when_the_session_ends(engine, tmp_path):
    _audit_on(engine, tmp_path)
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
    engine.run_tool("present_plan", {"plan": "write out.txt"})

    engine.finish_plan_session()

    assert engine._plan_approved_text == ""


def test_reads_stay_unaudited_even_with_a_plan_in_hand(engine, tmp_path):
    """Passing the plan as the criterion must not make every call auditable."""
    seen = _audit_on(engine, tmp_path)
    _approve(engine)
    (tmp_path / "r.txt").write_text("hi", encoding="utf-8")

    engine.exec_tool("read_text", json.dumps({"filename": str(tmp_path / "r.txt")}))

    assert seen == []
