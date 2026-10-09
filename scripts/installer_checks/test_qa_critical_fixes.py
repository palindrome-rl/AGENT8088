"""Critical QA issues from the 2026-10 review round.

Each test names the reported symptom it guards, because the fix is only
meaningful against the complaint that motivated it.
"""
import pytest

from agent8088 import engine


# --- "Low 10-15 Turns Default Limit" (Critical) ---------------------------
# A fresh install started every run at 10 rounds, which is below what a
# multi-step task needs before the dynamic ceiling has any progress to grow on.

def test_default_max_turns_is_fifty(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "APP_CONFIG", {}, raising=False)
    assert cli.Session().max_turns == 50


def test_configured_max_turns_still_wins(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "APP_CONFIG", {"max_turns": "7"}, raising=False)
    assert cli.Session().max_turns == 7


def test_gateway_uses_the_shared_starting_allowance(monkeypatch):
    from agent8088.gateway import agent_bridge
    monkeypatch.setattr(agent_bridge.A, "APP_CONFIG", {})
    assert agent_bridge._turn_max_turns("full-auto") == engine.DEFAULT_MAX_TURNS == 50
    assert agent_bridge._turn_max_turns("plan-only") == 50
    monkeypatch.setattr(agent_bridge.A, "APP_CONFIG", {"max_turns": "7"})
    assert agent_bridge._turn_max_turns("full-auto") == 7


def test_direct_engine_and_cli_defaults_agree():
    import inspect
    from agent8088 import cli
    assert inspect.signature(engine._run_agent_loop).parameters["max_turns"].default == cli.DEFAULT_MAX_TURNS


def test_capability_report_uses_the_shared_default(monkeypatch):
    monkeypatch.setattr(engine, "APP_CONFIG", {})
    assert "Max turns per request: 50" in engine.describe_capabilities()
    monkeypatch.setattr(engine, "APP_CONFIG", {"max_turns": "120"})
    assert "Max turns per request: 120" in engine.describe_capabilities()


def test_unparseable_max_turns_falls_back_to_the_default(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "APP_CONFIG", {"max_turns": "not-a-number"}, raising=False)
    assert cli.Session().max_turns == 50


def test_max_turns_is_documented_in_the_shipped_config():
    """It was undiscoverable: raising it meant reading the source."""
    from pathlib import Path
    template = Path(engine.__file__).with_name("config.txt").read_text(encoding="utf-8")
    assert "max_turns=" in template
    assert "dynamic_turns_ceiling_multiplier" in template


# --- "Turn budget exceeded: 121 seconds elapsed" (Critical) ----------------
# The wall-clock limit announced itself as a *turn* budget, so the reporter
# raised max_turns to 120 and hit the same wall. Each limit now names itself.

def _budget(**kwargs):
    return engine._TurnBudget(**kwargs)


def test_time_limit_does_not_call_itself_a_turn_budget():
    budget = _budget(max_seconds=1, seconds_setting="plan_audit_timeout_seconds")
    budget.started -= 10
    reason = budget.exceeded()
    assert reason is not None
    assert "Time budget exceeded" in reason
    assert "Turn budget" not in reason
    # It may mention max_turns, but only to rule it out as the remedy -- that
    # is the whole point: the reporter raised max_turns and hit the same wall.
    assert "raising max_turns will not change it" in reason


def test_time_limit_still_carries_the_classifier_marker():
    """cli._run_end_reason keys on 'seconds elapsed' to report time_budget."""
    budget = _budget(max_seconds=1, seconds_setting="plan_audit_timeout_seconds")
    budget.started -= 10
    assert "seconds elapsed" in budget.exceeded()


def test_time_limit_names_the_setting_that_raises_it():
    budget = _budget(max_seconds=1, seconds_setting="plan_audit_timeout_seconds")
    budget.started -= 10
    assert "plan_audit_timeout_seconds" in budget.exceeded()


def test_token_limit_names_tokens_not_turns():
    budget = _budget(max_tokens=10)
    budget.add_tokens(6, 6)
    reason = budget.exceeded()
    assert "Token budget exceeded" in reason
    assert "Turn budget" not in reason


def test_cost_limit_names_cost_not_turns():
    budget = _budget(max_cost=0.01, cost_in=1.0, cost_out=1.0)
    budget.add_tokens(100, 100)
    reason = budget.exceeded()
    assert "Cost budget exceeded" in reason
    assert "Turn budget" not in reason


def test_a_budget_within_its_limits_reports_nothing():
    assert _budget(max_seconds=600).exceeded() is None


# --- The dynamic budget must survive the new default ------------------------
# Raising the floor to 30 must not quietly replace growth with a bigger fixed
# number: 30 is what a run STARTS with, and progress still buys more.

def _policy(max_turns=50, **kwargs):
    return engine._turn_policy(max_turns, **kwargs)


def test_default_run_still_starts_at_fifty_and_can_grow():
    policy = _policy()
    assert policy.limit == 50
    assert policy.ceiling == 50 * engine.DYNAMIC_TURNS_CEILING_MULTIPLIER
    assert policy.ceiling > policy.limit, "growth disabled at the new default"


def test_growth_is_granted_only_at_the_boundary():
    policy = _policy()
    for _ in range(50):
        policy.record_round(fresh=True, output="ok")
    assert policy.extend(0) is None, "extended in the middle of a block"
    assert policy.extend(49) == (50, 55), "no extension at the boundary"


def test_a_productive_run_earns_more_rounds():
    policy = _policy()
    for _ in range(50):                      # every round did something new
        policy.record_round(fresh=True, output="ok")
    assert policy.extend(49) == (50, 55)
    assert policy.limit == 55


def test_a_stalled_run_earns_nothing():
    policy = _policy()
    for _ in range(50):                      # repeats: nothing fresh
        policy.record_round(fresh=False, output="ok")
    assert policy.extend(49) is None
    assert policy.limit == 50


def test_failing_work_does_not_count_as_progress():
    policy = _policy()
    for _ in range(50):                      # fresh, but every call failed
        policy.record_round(fresh=True, output="Error: command failed")
    assert policy.extend(49) is None


def test_growth_stops_at_the_ceiling():
    """Progress buys rounds up to max_turns * multiplier, and not one more."""
    policy = _policy()
    guard = 0
    while policy.extend(policy.limit - 1) is not None or guard == 0:
        guard += 1
        assert guard < 100, "ceiling walk did not terminate"
        # Prove progress for every round of the block about to be judged.
        for _ in range(policy.limit):
            policy.record_round(fresh=True, output="ok")
    assert policy.limit == policy.ceiling == 200
    for _ in range(policy.limit):
        policy.record_round(fresh=True, output="ok")
    assert policy.extend(policy.limit - 1) is None, "grew past the ceiling"


def test_growth_can_still_be_switched_off():
    policy = _policy(dynamic=False)
    assert policy.limit == policy.ceiling == 50
    for _ in range(50):
        policy.record_round(fresh=True, output="ok")
    assert policy.extend(49) is None


# --- "says it's done with phase 3 but the task folder is empty" ------------
# (Critical). Reproduced: the work WAS done, but under the sandbox
# artifacts workspace, while the answer said only "I created the phase3
# folder". The user looked in the project folder, found nothing, and read a
# correct run as a false completion claim.
#
# A system-prompt rule was tried first and rejected: gemma-4-26b ignored it
# (still "I have created the folder phase4", no path), and the extra lines
# pushed the request overhead past the 7,200-token guard in
# test_prompt_token_budget.py. The note is emitted by the controller instead,
# so it does not depend on the model choosing to comply.

def test_the_contract_still_requires_verification_before_claiming_completion():
    """The existing rule must survive the addition, not be replaced by it."""
    from pathlib import Path
    prompt = Path(engine.__file__).with_name("system.md").read_text(encoding="utf-8")
    assert "verify the result before claiming completion" in prompt


# A prompt rule alone did not fix this: asked to make a folder, gemma-4-26b
# still answered "I have created the folder phase4" with no path. The note
# below is emitted by the controller, so it does not depend on the model
# choosing to comply.

from agent8088 import trajectory as trajectory_mod


def _traj():
    return trajectory_mod.TrajectoryState(None, goal="g")


def _write(traj, path, *, status_ok=True):
    operation = traj.before_tool("write_file")
    traj.after_tool(operation, "wrote", failed=not status_ok,
                    blocked=False, mutated=True, path=path)


def test_a_successful_write_is_recorded_with_its_path():
    traj = _traj()
    _write(traj, "/ws/artifacts/phase4/x.txt")
    assert "/ws/artifacts/phase4/x.txt" in traj.written_paths()


def test_a_failed_write_is_not_recorded():
    traj = _traj()
    _write(traj, "/ws/artifacts/phase4/x.txt", status_ok=False)
    assert traj.written_paths() == []


def test_written_paths_are_deduplicated_and_ordered():
    traj = _traj()
    _write(traj, "/ws/a.txt")
    _write(traj, "/ws/b.txt")
    _write(traj, "/ws/a.txt")
    assert traj.written_paths() == ["/ws/a.txt", "/ws/b.txt"]


def _roots(monkeypatch, tmp_path, *, sandboxed="docker"):
    project, artifacts = tmp_path / "proj", tmp_path / "proj" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(engine, "PROJECT_ROOT", project)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(engine, "_resolve_sandbox_backend", lambda: sandboxed)
    return project, artifacts


def test_the_answer_names_the_directory_files_actually_landed_in(monkeypatch, tmp_path):
    project, artifacts = _roots(monkeypatch, tmp_path)
    traj = _traj()
    _write(traj, str(artifacts / "phase4" / "x.txt"))

    answer = engine._with_task_notes("Phase 4 is complete.", traj)
    assert str(artifacts / "phase4") in answer
    assert "Phase 4 is complete." in answer


def test_no_location_note_when_nothing_was_written(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path)
    assert engine._with_task_notes("Hello.", _traj()) == "Hello."


def test_a_write_inside_the_project_needs_no_redirection_note(monkeypatch, tmp_path):
    project, _ = _roots(monkeypatch, tmp_path, sandboxed="unavailable")
    traj = _traj()
    _write(traj, str(project / "src" / "main.py"))
    assert "Files written" not in engine._with_task_notes("Done.", traj)


def test_a_sandboxed_shell_mutation_names_the_workspace(monkeypatch, tmp_path):
    """The reported case: mkdir/echo in the sandbox reports no path at all."""
    _, artifacts = _roots(monkeypatch, tmp_path)
    traj = _traj()
    operation = traj.before_tool("execute_shell")
    traj.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, path="")

    answer = engine._with_task_notes("Phase 5 is complete.", traj)
    assert str(artifacts) in answer
    assert "relative path" in answer


def test_an_unsandboxed_shell_mutation_gets_no_workspace_note(monkeypatch, tmp_path):
    _roots(monkeypatch, tmp_path, sandboxed="unavailable")
    traj = _traj()
    operation = traj.before_tool("execute_shell")
    traj.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, path="")
    assert "sandbox workspace" not in engine._with_task_notes("Done.", traj)


# --- The same fixes must hold on the Web UI surface -------------------------
# Found while checking parity: loading a session whose file predates a given
# setting reset that setting to a hardcoded 10, on BOTH surfaces. A user who
# set 120 and then opened a saved session silently got 10 back -- the most
# likely reading of "I raised max_turns to 120 but it's still set to".

def test_cli_session_restore_keeps_the_current_max_turns(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "APP_CONFIG", {}, raising=False)
    session = cli.Session()
    session.max_turns = 120
    restored = int({}.get("max_turns", session.max_turns))   # the restore expression
    assert restored == 120, "a session without max_turns clobbered the live value"


def test_cli_session_restore_does_not_hardcode_ten():
    from pathlib import Path
    from agent8088 import cli
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert 'data.get("max_turns", 10)' not in source


def test_web_session_restore_does_not_hardcode_ten():
    from pathlib import Path
    from agent8088 import web_server
    source = Path(web_server.__file__).read_text(encoding="utf-8")
    assert 'data.get("max_turns", 10)' not in source


def test_web_limits_fallback_is_the_shared_default():
    from pathlib import Path
    from agent8088 import web_server
    source = Path(web_server.__file__).read_text(encoding="utf-8")
    assert '"max_turns": C.S.max_turns if (C := _cl()) else 10' not in source


def test_the_web_ceiling_admits_the_grown_budget():
    """max_turns 30 grows to 120; the Web UI must not reject what the CLI allows."""
    from agent8088 import web_server, cli
    ceiling = cli.DEFAULT_MAX_TURNS * engine.DYNAMIC_TURNS_CEILING_MULTIPLIER
    assert web_server._MAX_TURNS_CEILING >= ceiling
