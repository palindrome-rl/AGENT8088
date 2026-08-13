"""`/plan` is a mode, not a one-shot.

A plan is text a human reads and approves; execution is what happens after. The
tests below pin that separation, and pin the two ways it used to leak: a grant
that outlived its mode, and a plan that was never run being reported as done.
"""
import io
import sys

import pytest
from rich.console import Console

import agent8088.cli as cli

PLAN = "## Goal\nAdd a greeting file.\n\n1. Write a.txt containing 'a'\n2. Read it back"


@pytest.mark.parametrize("args", [["--mode", "plan-only"], ["--edit"]])
def test_startup_rejects_mode_shortcuts_that_bypass_plan_state(args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent8088", *args])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Mode changes and the plan-session lifecycle
# ---------------------------------------------------------------------------

def test_setting_a_mode_drops_every_grant_tied_to_the_old_one(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_execution_grant = True
    engine._one_shot_grant = True

    engine.set_permission_mode("readonly")

    assert engine.PERMISSION_MODE == "readonly"
    assert engine._plan_execution_grant is False
    assert engine._one_shot_grant is False


def test_entering_plan_mode_remembers_where_to_return(engine):
    engine.PERMISSION_MODE = "full-auto"

    engine.enter_plan_mode()

    assert engine.PERMISSION_MODE == "plan-only"
    assert engine._plan_return_mode == "full-auto"


def test_entering_plan_mode_twice_keeps_the_original_return_mode(engine):
    """Re-entering must not make plan-only itself the place to come back to."""
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine.enter_plan_mode()

    assert engine._plan_return_mode == "readonly"


def test_cancelling_a_plan_session_forgets_the_pending_plan(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_approved = True

    engine.cancel_plan_session()

    assert engine._plan_return_mode == ""
    assert engine._plan_approved is False


# ---------------------------------------------------------------------------
# The present_plan tool
# ---------------------------------------------------------------------------

def test_present_plan_is_a_plan_mode_tool_taking_prose(engine):
    spec = engine.TOOL_SPECS["present_plan"]

    assert spec["mode"] == "plan"
    assert spec["args"] == ["plan"], "the plan is text, not a step array"


def test_present_plan_needs_the_plan_text(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = lambda text: "full-auto"

    assert "requires a non-empty 'plan'" in engine._exec_present_plan({})


def test_present_plan_is_refused_outside_plan_mode(engine):
    engine.PERMISSION_MODE = "full-auto"
    engine._plan_on_approval = lambda text: "full-auto"

    out = engine._exec_present_plan({"plan": PLAN})

    assert "only applies in plan mode" in out


def test_present_plan_says_so_when_there_is_nobody_to_approve(engine):
    """A non-interactive run must not silently self-approve."""
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = None

    out = engine._exec_present_plan({"plan": PLAN})

    assert "not approved" in out
    assert engine.PERMISSION_MODE == "plan-only"


def test_approval_switches_the_mode_and_tells_the_model_to_execute(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    seen = []
    engine._plan_on_approval = lambda text: seen.append(text) or "full-auto"

    out = engine._exec_present_plan({"plan": PLAN})

    assert seen == [PLAN], "the user is shown the plan, verbatim"
    assert engine.PERMISSION_MODE == "full-auto"
    assert engine._plan_approved is True
    assert "ordinary tool calls" in out


def test_denial_keeps_plan_mode_so_the_plan_can_be_revised(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: ""

    out = engine._exec_present_plan({"plan": PLAN})

    assert engine.PERMISSION_MODE == "plan-only"
    assert engine._plan_approved is False
    assert "revise" in out.lower()


def test_run_tool_routes_present_plan_to_the_approval_path(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = lambda text: "full-auto"

    out = engine.run_tool("present_plan", {"plan": PLAN})

    assert "Plan approved" in out
    assert engine.PERMISSION_MODE == "full-auto"


# ---------------------------------------------------------------------------
# Leaving plan mode
# ---------------------------------------------------------------------------

def test_finishing_an_approved_plan_returns_to_the_pre_plan_mode(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    engine._plan_on_approval = lambda text: "full-auto"
    engine._exec_present_plan({"plan": PLAN})

    restored = engine.finish_plan_session()

    assert restored == "readonly"
    assert engine.PERMISSION_MODE == "readonly"
    assert engine._plan_return_mode == ""


def test_finishing_does_nothing_while_the_plan_is_still_unapproved(engine):
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()

    assert engine.finish_plan_session() == ""
    assert engine.PERMISSION_MODE == "plan-only", "an unapproved plan stays in plan mode"


def test_plan_tool_ran_is_turn_scoped(engine):
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = lambda text: ""
    engine._exec_present_plan({"plan": PLAN})
    assert engine.plan_tool_ran() is True

    engine.reset_turn_counters()

    assert engine.plan_tool_ran() is False


# ---------------------------------------------------------------------------
# What a blocked tool says, and what an unclassifiable step does
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("write_file", {"filename": "x.txt", "content": "x"}),
    ("execute_shell", {"command": "ls"}),
])
def test_a_blocked_tool_in_plan_mode_points_at_present_plan(engine, tool, args):
    """The old message named execute_plan and a JSON step array — the one thing a
    model reliably gets wrong — so it retried the direct call until the loop died."""
    engine.PERMISSION_MODE = "plan-only"

    out = engine.run_tool(tool, args)

    assert "present_plan" in out
    assert "execute_plan" not in out


def test_an_unclassifiable_step_names_no_tool(engine):
    """A tie at score zero used to return whatever tool iterated first, and
    _infer_step_args then handed the step's prose to it as its one argument."""
    assert engine.classify_plan_component("") == ""


def test_prose_steps_do_not_become_shell_commands(engine, tmp_path):
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "full-auto"
    ran = []
    engine._exec_process = lambda *a, **k: ran.append(a) or "ok"

    out = engine._exec_plan({"steps": "[1, 2, 3]"})

    assert ran == [], "a bare number is not a command to run"
    assert "names no tool" in out


# ---------------------------------------------------------------------------
# The slash commands
# ---------------------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=120, color_system=None))
    return out


def test_slash_plan_with_no_task_just_enters_plan_mode(monkeypatch, captured):
    calls = []
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(cli, "do_chat", lambda task: calls.append(task))

    cli.cmd_plan("")

    assert calls == [], "no task means no model call"
    assert cli.A.PERMISSION_MODE == "plan-only"
    assert "plan mode" in captured.getvalue()


def test_slash_plan_with_a_task_sends_it_in_plan_mode(monkeypatch, captured):
    seen = []
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(cli, "do_chat",
                        lambda task: seen.append((task, cli.A.PERMISSION_MODE)))

    cli.cmd_plan("add a health endpoint")

    assert seen == [("add a health endpoint", "plan-only")]


def test_slash_plan_does_not_restore_the_mode_when_the_turn_ends(monkeypatch, captured):
    """The whole point: plan mode outlives the turn that started it."""
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(cli, "do_chat", lambda task: None)

    cli.cmd_plan("add a health endpoint")

    assert cli.A.PERMISSION_MODE == "plan-only"


def test_mode_no_longer_offers_plan_only(monkeypatch, captured):
    """`/plan` is the only door into a plan session.

    A plan session has a beginning and an end — propose, approve, run, return to
    the mode you came from. Offering it as a `/mode` setting let a user enter one
    and walk away from it by hand, leaving the mode it was meant to restore
    stranded. It redirects rather than erroring, because typing it is reasonable.
    """
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "full-auto")

    cli.cmd_mode("plan-only")

    assert cli.A.PERMISSION_MODE == "full-auto", "the mode must not change"
    assert "/plan" in captured.getvalue()


def test_mode_lists_only_the_settable_modes(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")

    cli.cmd_mode("")

    rendered = captured.getvalue()
    assert "readonly" in rendered and "full-auto" in rendered
    assert "Valid modes: readonly, full-auto" in rendered
    assert "/plan" in rendered, "the way into plan mode still needs signposting"


@pytest.mark.parametrize("mode", ["readonly", "full-auto", "edit"])
def test_the_real_modes_still_switch(monkeypatch, captured, mode):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")

    cli.cmd_mode(mode)

    assert cli.A.PERMISSION_MODE == ("full-auto" if mode == "edit" else mode)


def test_leaving_plan_mode_by_hand_cancels_the_pending_plan(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.cmd_plan("")

    cli.cmd_mode("full-auto")

    assert cli.A.PERMISSION_MODE == "full-auto"
    assert cli.A._plan_return_mode == "", "no plan is pending, so nothing to return to"


# ---------------------------------------------------------------------------
# The approval prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer,expected", [
    ("a", "full-auto"),
    ("e", "readonly"),
    ("d", ""),
    ("", ""),
])
def test_the_approval_prompt_maps_answers_to_destination_modes(
        monkeypatch, captured, answer, expected):
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: answer)

    approve = cli._make_plan_approval(live=None)

    assert approve("## Goal\nDo the thing\n\n1. Write a.txt") == expected
    assert "Do the thing" in captured.getvalue(), "the user sees the plan"


# ---------------------------------------------------------------------------
# End-of-turn bookkeeping
# ---------------------------------------------------------------------------

def test_an_approved_plan_reports_the_mode_coming_back(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.A.enter_plan_mode()
    monkeypatch.setattr(cli.A, "_plan_on_approval", lambda text: "full-auto")
    cli.A._exec_present_plan({"plan": "1. write a.txt"})

    cli._after_turn_plan_state()

    assert cli.A.PERMISSION_MODE == "readonly"
    assert "back to readonly" in captured.getvalue()


def test_a_turn_that_planned_nothing_says_nothing_happened(monkeypatch, captured):
    """Repro B: the model described a plan, ran nothing, then reported it done."""
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.A.enter_plan_mode()
    cli.A.reset_turn_counters()

    cli._after_turn_plan_state()

    assert cli.A.PERMISSION_MODE == "plan-only"
    assert "nothing above was written or run" in captured.getvalue()


def test_a_turn_that_presented_a_plan_does_not_warn(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.A.enter_plan_mode()
    monkeypatch.setattr(cli.A, "_plan_on_approval", lambda text: "")
    cli.A._exec_present_plan({"plan": "1. write a.txt"})

    cli._after_turn_plan_state()

    assert "nothing above was written or run" not in captured.getvalue()


# ---------------------------------------------------------------------------
# Turn budget, UI, and the system prompt
# ---------------------------------------------------------------------------

def test_a_plan_mode_turn_gets_room_to_research_plan_and_execute():
    """Approval lands mid-turn, so the plan's steps spend the same turn budget the
    research already spent. 10 rounds is not enough for both."""
    assert cli._turn_max_turns("readonly") == cli.S.max_turns
    assert cli._turn_max_turns("plan-only") >= 25


def test_plan_mode_exposes_only_research_and_approval_tools(monkeypatch):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    specs = cli._active_tool_specs()

    assert {"present_plan", "read_text", "git_status"} <= set(specs)
    assert "write_file" not in specs
    assert "execute_shell" not in specs


def test_plan_mode_tool_and_prompt_providers_refresh_after_approval(
        engine, tmp_path, monkeypatch, scripted):
    engine.PROJECT_ROOT = tmp_path
    engine.ARTIFACTS_ROOT = tmp_path / "artifacts"
    engine.ALLOWED_PATHS = [tmp_path]
    engine.PERMISSION_MODE = "plan-only"
    engine._plan_on_approval = lambda _plan: "full-auto"
    target = tmp_path / "artifacts" / "out.txt"
    model = scripted([
        '✿FUNCTION✿: present_plan ✿ARGS✿: {"plan": "1. write out.txt"}',
        '✿FUNCTION✿: write_file ✿ARGS✿: '
        + __import__("json").dumps({"filename": str(tmp_path / "out.txt"), "content": "done"}),
        "done",
    ])
    monkeypatch.setattr(engine, "create_completion", model)

    def allowed():
        return ({"present_plan"} if engine.PERMISSION_MODE == "plan-only"
                else {"write_file"})

    answer = engine.run_agent(
        [{"role": "user", "content": "make it"}], max_turns=4,
        tools_def=lambda: [], allowed_tools=allowed,
        system_prompt=lambda: f"mode={engine.PERMISSION_MODE}",
    )

    assert answer == "done"
    assert target.read_text() == "done"
    assert model.calls[0]["kwargs"]["system_prompt"] == "mode=plan-only"
    assert model.calls[1]["kwargs"]["system_prompt"] == "mode=full-auto"


def test_plan_mode_stops_after_bounded_invalid_mutation_retries(
        engine, monkeypatch, scripted):
    engine.PERMISSION_MODE = "plan-only"
    engine.PLAN_MODE_RETRY_LIMIT = 2
    model = scripted([
        '✿FUNCTION✿: write_file ✿ARGS✿: {"filename":"one.txt","content":"x"}',
        '✿FUNCTION✿: write_file ✿ARGS✿: {"filename":"two.txt","content":"x"}',
        '✿FUNCTION✿: write_file ✿ARGS✿: {"filename":"three.txt","content":"x"}',
    ])
    monkeypatch.setattr(engine, "create_completion", model)

    answer = engine.run_agent([{"role": "user", "content": "plan it"}], max_turns=25)

    assert len(model.calls) == 2
    assert "stopped" in answer.lower()
    assert "nothing was written" in answer.lower()


def test_plan_mode_has_a_default_wall_clock_limit(engine, monkeypatch):
    seen = {}
    engine.PERMISSION_MODE = "plan-only"
    engine.MAX_TURN_SECONDS = 0
    engine.PLAN_MODE_TIMEOUT_SECONDS = 123
    monkeypatch.setattr(
        engine, "_run_agent_loop",
        lambda *_args, budget=None, **_kwargs: seen.setdefault("seconds", budget.max_seconds),
    )

    assert engine.run_agent([]) == 123
    assert seen == {"seconds": 123}


def test_the_prompt_shows_when_you_are_in_plan_mode(monkeypatch):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    assert "plan" in cli._prompt_label()

    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    assert "plan" not in cli._prompt_label()


def test_model_plan_only_points_at_slash_plan(monkeypatch, captured):
    """It used to advise `/mode plan-only`, which no longer exists."""
    cli.cmd_model("plan-only")

    rendered = captured.getvalue()
    assert "/plan" in rendered
    assert "/mode plan-only" not in rendered


def test_the_system_prompt_teaches_present_plan_not_json_steps(engine):
    prompt = engine.SYSTEM_PROMPT

    assert "present_plan" in prompt
    assert "## Plan Mode" in prompt


def test_a_subagent_cannot_take_the_session_out_of_plan_mode(engine):
    """The plan belongs to the main agent's turn. A delegated sub-task asking the
    user to approve *its* plan would leave plan mode on the strength of an approval
    the user gave for something else. No shipped profile can reach present_plan,
    but a hand-written one could, and the hole would be silent."""
    engine.PERMISSION_MODE = "readonly"
    engine.enter_plan_mode()
    asked = []
    engine._plan_on_approval = lambda text: asked.append(text) or "full-auto"

    out = engine.run_tool("present_plan", {"plan": PLAN}, depth=1)

    assert asked == [], "a subagent must not get to ask"
    assert engine.PERMISSION_MODE == "plan-only"
    assert engine._plan_approved is False
    assert "sub-agent" in out
