"""Plan halt-on-failure and the sub-agent readonly permission floor.

Both cover the same failure: a step that did not do what it claimed, recorded as
though it had. The plan tests stop the cascade; the floor tests make the auditor
that catches it structurally incapable of changing what it is inspecting.
"""
import json

from tests.conftest import ScriptedModel


# ---------------------------------------------------------------------------
# _exec_plan — a failed step stops the plan
# ---------------------------------------------------------------------------

def test_plan_halts_and_skips_remaining_steps_after_a_failure(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    later = tmp_path / "later.txt"
    result = engine._exec_plan({"steps": json.dumps([
        {"tool": "read_text", "arguments": {"filename": str(tmp_path / "missing.txt")}},
        {"tool": "write_file", "arguments": {"filename": str(later), "content": "x"}},
    ])})
    assert not later.exists(), "step after a failure must not run"
    assert "halted at step 1/2" in result
    assert "remaining 1 step(s) were NOT run" in result


def test_plan_runs_every_step_when_none_fail(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    result = engine._exec_plan({"steps": json.dumps([
        {"tool": "write_file", "arguments": {"filename": str(first), "content": "1"}},
        {"tool": "write_file", "arguments": {"filename": str(second), "content": "2"}},
    ])})
    assert first.read_text() == "1" and second.read_text() == "2"
    assert "halted" not in result


def test_plan_halts_when_an_escalation_is_denied(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PROMPT_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "readonly"
    later = tmp_path / "after-denial.txt"
    result = engine._exec_plan(
        {"steps": json.dumps([
            {"tool": "write_file",
             "arguments": {"filename": str(tmp_path / "denied.txt"), "content": "x"}},
            {"tool": "write_file", "arguments": {"filename": str(later), "content": "x"}},
        ])},
        on_escalation=lambda _request: False,  # user says no
    )
    assert not later.exists(), "a denied step must not be followed by more steps"
    assert "halted at step 1/2" in result


def test_plan_halts_on_unknown_tool(engine):
    result = engine._exec_plan({"steps": json.dumps([
        {"tool": "no_such_tool", "arguments": {}},
        {"tool": "calculate", "arguments": {"expression": "1+1"}},
    ])})
    assert "unknown tool" in result
    assert "halted at step 1/2" in result
    assert "[2]" not in result


def test_plan_step_failed_recognises_errors_and_unanswered_escalations(engine):
    assert engine._plan_step_failed("Error: boom") is True
    assert engine._plan_step_failed("ESCALATION_REQUEST:edit:write:/x:why") is True
    assert engine._plan_step_failed("Wrote 12 bytes to /tmp/x") is False
    assert engine._plan_step_failed("") is False


# ---------------------------------------------------------------------------
# Sub-agent permission floor
# ---------------------------------------------------------------------------

def _floor_probe(engine, name="floor-probe"):
    """Register a profile that CAN call write_file but declares a readonly floor.

    The shipped auditor leaves write_file out of its tool list, so a write there
    is dropped by the tool restriction before the permission layer is consulted.
    Testing the floor against that profile passes even with the floor deleted, so
    these tests need a profile where the floor is the only thing in the way.
    """
    engine.SUBAGENT_SPECS[name] = {
        "name": name,
        "description": "floor probe",
        "tools": ["read_text", "write_file", "last_output"],
        "max_turns": 4,
        "permission": "readonly",
        "system_prompt": "You are a probe.",
    }
    return name


def _write_then_finish(engine, target):
    return ScriptedModel([
        '✿FUNCTION✿: write_file ✿ARGS✿: '
        + json.dumps({"filename": str(target), "content": "should not land"}),
        "VERDICT: unknown — could not write.",
    ])


def test_auditor_profile_declares_a_readonly_floor(engine):
    profile = engine.SUBAGENT_SPECS["auditor"]
    assert profile["permission"] == "readonly"
    assert "write_file" not in profile["tools"]


def test_profile_without_a_permission_key_is_unrestricted(engine, tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "plain.md").write_text(
        "---\nname: plain\ntools: read_text\n---\nBody.\n", encoding="utf-8")
    assert engine.load_subagent_specs(d)["plain"]["permission"] == ""


def test_readonly_floor_blocks_a_write_the_parent_mode_would_allow(engine, monkeypatch, tmp_path):
    """The real guarantee: full-auto parent, floored sub-agent still cannot write."""
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    target = tmp_path / "should-not-be-written.txt"
    monkeypatch.setattr(engine, "create_completion", _write_then_finish(engine, target))
    engine._exec_subagent({"agent_type": _floor_probe(engine), "task": "check"}, depth=0)
    assert not target.exists(), "readonly floor must refuse a write the parent mode allows"


def test_readonly_floor_applies_during_the_run_and_restores_after(engine, monkeypatch):
    engine.PERMISSION_MODE = "full-auto"
    during = []
    monkeypatch.setattr(engine, "run_agent",
                        lambda *_a, **_k: during.append(engine.PERMISSION_MODE) or "ok")
    engine._exec_subagent({"agent_type": _floor_probe(engine), "task": "check"}, depth=0)
    assert during == ["readonly"], "sub-run must execute under the floor"
    assert engine.PERMISSION_MODE == "full-auto", "parent mode must be restored"


def test_readonly_floor_clears_pending_grants_for_the_sub_run(engine, monkeypatch):
    """A grant the PARENT holds must not be live inside a floored sub-agent.

    Asserted on the grant flags during the sub-run rather than on a write that
    fails to land: `run_tool` blocks direct calls outright in plan-only, and the
    path check can refuse a write for unrelated reasons, so an end-to-end write
    would still pass here with the grant leak present.
    """
    engine.PERMISSION_MODE = "readonly"
    engine._one_shot_grant = True        # user just approved something for the parent
    engine._plan_execution_grant = True  # parent is mid-approved-plan
    engine._local_fallback_grant = True
    engine._remote_git_grant = True

    seen = {}

    def _spy(*_args, **_kwargs):
        seen.update(
            mode=engine.PERMISSION_MODE,
            one_shot=engine._one_shot_grant,
            plan=engine._plan_execution_grant,
            local=engine._local_fallback_grant,
            git=engine._remote_git_grant,
        )
        return "ok"

    monkeypatch.setattr(engine, "run_agent", _spy)
    engine._exec_subagent({"agent_type": _floor_probe(engine), "task": "check"}, depth=0)

    assert seen == {"mode": "readonly", "one_shot": False, "plan": False,
                    "local": False, "git": False}, "no parent grant may be live in the sub-run"
    # ...and every one of them is handed back to the parent afterwards.
    assert engine._one_shot_grant is True
    assert engine._plan_execution_grant is True
    assert engine._local_fallback_grant is True
    assert engine._remote_git_grant is True


def test_stale_one_shot_grant_would_otherwise_permit_a_write(engine):
    """Why the clearing above matters: the gate really does honour that grant."""
    engine.PERMISSION_MODE = "readonly"
    engine._one_shot_grant = False
    assert engine.check_permission("write_text") is False
    engine._one_shot_grant = True
    assert engine.check_permission("write_text") is True


def test_floor_never_widens_a_restricted_parent(engine, monkeypatch):
    """A profile cannot grant itself more than the caller had."""
    engine.PERMISSION_MODE = "readonly"
    engine.SUBAGENT_SPECS["widen"] = dict(
        engine.SUBAGENT_SPECS["auditor"], name="widen", permission="full-auto")
    monkeypatch.setattr(engine, "create_completion", ScriptedModel(["done"]))
    seen = []
    monkeypatch.setattr(engine, "run_agent",
                        lambda *_a, **_k: seen.append(engine.PERMISSION_MODE) or "done")
    engine._exec_subagent({"agent_type": "widen", "task": "try to widen"}, depth=0)
    assert seen == ["readonly"], "an unrecognised permission value must not widen the sub-run"
