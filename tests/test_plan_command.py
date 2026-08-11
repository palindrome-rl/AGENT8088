"""`/plan` is a mode, not a one-shot.

A plan is text a human reads and approves; execution is what happens after. The
tests below pin that separation, and pin the two ways it used to leak: a grant
that outlived its mode, and a plan that was never run being reported as done.
"""
import io

import pytest
from rich.console import Console

import agent8088.cli as cli

PLAN = "## Goal\nAdd a greeting file.\n\n1. Write a.txt containing 'a'\n2. Read it back"


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


def test_mode_plan_only_starts_the_same_plan_session_as_slash_plan(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "full-auto")

    cli.cmd_mode("plan-only")

    assert cli.A.PERMISSION_MODE == "plan-only"
    assert cli.A._plan_return_mode == "full-auto"


def test_leaving_plan_mode_by_hand_cancels_the_pending_plan(monkeypatch, captured):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    cli.cmd_mode("plan-only")

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


def test_the_prompt_shows_when_you_are_in_plan_mode(monkeypatch):
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    assert "plan" in cli._prompt_label()

    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "readonly")
    assert "plan" not in cli._prompt_label()


def test_model_plan_only_points_at_the_mode_command(monkeypatch, captured):
    cli.cmd_model("plan-only")

    rendered = captured.getvalue()
    assert "/plan" in rendered or "/mode plan-only" in rendered


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
