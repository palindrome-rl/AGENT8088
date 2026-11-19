"""In plan mode, a reply that did not come through present_plan is offered to the user as the plan.

Seen in real runs: the model answered in plan mode with prose, a research summary or a question and never called
present_plan. The turn ended with no approval prompt, the session stayed in plan mode, and the user had to guess what
to type next. The user chose plan mode and decides what counts as a plan, so the reply itself is put up for approval:
approving runs it as the plan, declining leaves the session in plan mode for the user to answer or ask for a revision.
"""
from contextlib import nullcontext

import pytest


def _completion(replies, seen=None):
    calls = []

    def completion(*args, **kwargs):
        if seen is not None:
            sent = kwargs.get("messages") or (args[0] if args else [])
            seen.append([dict(m) for m in sent])
        text = replies[min(len(calls), len(replies) - 1)]
        calls.append(True)
        message = type("Message", (), {"content": text})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        return type("Response", (), {"choices": [choice]})()
    return completion, calls


@pytest.fixture
def planning(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "BLOCKED_PATHS", [])
    monkeypatch.setattr(engine, "_human_wait", nullcontext)
    engine.enter_plan_mode()
    return engine


def _approver(decision, shown):
    def approve(plan_text, source="tool"):
        shown.append((plan_text, source))
        return decision
    return approve


def _run(engine, monkeypatch, replies, request="Plan a small web app, then wait for approval.", seen=None,
         history=None):
    completion, calls = _completion(replies, seen)
    monkeypatch.setattr(engine, "_create_completion_with_fallback", completion)
    messages = list(history or []) + [{"role": "user", "content": request}]
    trace = []
    answer = engine.run_agent(messages, max_turns=6, tools_def=[], allowed_tools={"present_plan"},
                              system_prompt="", memory_capture=False, trace=trace)
    return answer, calls, messages, trace


SUMMARY = "I looked at the project. It is a Flask app with two routes and no tests yet."
TOOL_CALL = '✿FUNCTION✿: calculate ✿ARGS✿: {"expression": "1+1"}'
QUESTION = "Should the new page use Flask templates or a separate React front end?"


@pytest.mark.parametrize("reply", [SUMMARY, QUESTION, "1. Add the route.\n2. Add the template.\n3. Test it."])
def test_a_reply_without_present_plan_is_offered_as_the_plan(planning, monkeypatch, reply):
    shown = []
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("", shown), raising=False)
    answer, calls, _messages, trace = _run(planning, monkeypatch, [reply])
    assert shown == [(reply, "reply")]
    assert len(calls) == 1, "a declined reply ends the turn; the model is not asked again"
    assert reply in answer
    assert planning.PERMISSION_MODE == "plan-only"
    assert any(step["type"] == "reply_offered_as_plan" and step["approved"] is False for step in trace)
    assert not any(step["type"] == "plan_text_nudge" for step in trace), "an offered reply needs no nudge"


def test_a_declined_reply_is_not_saved_as_the_projects_plan(planning, monkeypatch, tmp_path):
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("", []), raising=False)
    _run(planning, monkeypatch, [QUESTION])
    assert not list(tmp_path.glob("PLAN*.md"))


def test_an_approved_reply_runs_as_the_plan_with_the_original_request(planning, monkeypatch, tmp_path):
    shown, seen = [], []
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", shown), raising=False)
    answer, calls, messages, trace = _run(
        planning, monkeypatch, [SUMMARY, TOOL_CALL, "All steps are done."],
        request="Add an about page to the Flask app.", seen=seen)
    assert len(shown) == 1
    assert len(calls) >= 2, "approval continues the same request so the work runs"
    assert planning.PERMISSION_MODE == "full-auto"
    assert planning._plan_approved is True
    assert planning._plan_approved_text == SUMMARY
    handoff = seen[1][-1]["content"]
    assert "approved" in handoff.lower()
    assert "Add an about page to the Flask app." in handoff, "the approved plan is sent with the original request"
    assert (tmp_path / "PLAN.md").read_text(encoding="utf-8").strip() == SUMMARY
    assert any(step["type"] == "reply_offered_as_plan" and step["approved"] is True for step in trace)


def test_readonly_approval_is_passed_through(planning, monkeypatch):
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("readonly", []), raising=False)
    _run(planning, monkeypatch, [SUMMARY, TOOL_CALL, "Done."])
    assert planning.PERMISSION_MODE == "readonly"
    assert planning._plan_approved is True


def test_requests_made_while_planning_travel_with_the_approved_plan(planning, monkeypatch):
    """The user answers a clarifying question and then approves: both requests are part of what was agreed."""
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("", []), raising=False)
    first_answer, _calls, first_messages, _trace = _run(
        planning, monkeypatch, [QUESTION], request="Add an about page to the Flask app.")
    assert planning.PERMISSION_MODE == "plan-only"

    seen = []
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", []), raising=False)
    history = first_messages + [{"role": "assistant", "content": first_answer}]
    _run(planning, monkeypatch, ["1. Add templates/about.html.\n2. Add the /about route.", "Done."],
         request="Use Flask templates.", seen=seen, history=history)
    handoff = seen[1][-1]["content"]
    assert "Add an about page to the Flask app." in handoff
    assert "Use Flask templates." in handoff


def test_a_plan_session_starts_with_no_requests_left_from_an_earlier_one(planning, monkeypatch):
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("", []), raising=False)
    _run(planning, monkeypatch, [QUESTION], request="An old task nobody approved.")
    planning.cancel_plan_session()
    planning.enter_plan_mode()
    seen = []
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", []), raising=False)
    _run(planning, monkeypatch, [SUMMARY, "Done."], request="The new task.", seen=seen)
    handoff = seen[1][-1]["content"]
    assert "The new task." in handoff
    assert "An old task nobody approved." not in handoff


def test_an_empty_reply_is_never_offered_as_a_plan(planning, monkeypatch):
    shown = []
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", shown), raising=False)
    _run(planning, monkeypatch, ["", ""])
    assert shown == []
    assert planning.PERMISSION_MODE == "plan-only"


def test_a_one_argument_approval_callback_still_works(planning, monkeypatch):
    """Embedders wrote callbacks that take only the plan text; they keep working."""
    shown = []
    monkeypatch.setattr(planning, "_plan_on_approval", lambda text: shown.append(text) or "", raising=False)
    _run(planning, monkeypatch, [SUMMARY])
    assert shown == [SUMMARY]


def test_no_offer_without_a_way_to_ask(planning, monkeypatch):
    """Non-interactive runs (gateway, cron, MCP) have nobody to approve; they keep the present_plan nudge."""
    monkeypatch.setattr(planning, "_plan_on_approval", None, raising=False)
    _answer, _calls, _messages, trace = _run(planning, monkeypatch, [SUMMARY])
    assert not any(step["type"] == "reply_offered_as_plan" for step in trace)
    assert planning.PERMISSION_MODE == "plan-only"


def test_no_offer_outside_plan_mode(engine, monkeypatch):
    monkeypatch.setattr(engine, "PERMISSION_MODE", "readonly")
    shown = []
    monkeypatch.setattr(engine, "_plan_on_approval", _approver("full-auto", shown), raising=False)
    _run(engine, monkeypatch, [SUMMARY])
    assert shown == []
    assert engine.PERMISSION_MODE == "readonly"


def test_present_plan_still_labels_its_plan_as_a_tool_plan(planning, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("", shown), raising=False)
    planning._exec_present_plan({"plan": "1. Do it."})
    assert shown == [("1. Do it.", "tool")]


# --- CLI ---------------------------------------------------------------------------------------------------

def _cli_prompt(monkeypatch, answer):
    from agent8088 import cli
    shown, prompts = [], []
    monkeypatch.setattr(cli.console, "input", lambda prompt, *a, **k: prompts.append(str(prompt)) or answer)

    def capture(*items, **_kwargs):
        for item in items:
            renderable = getattr(item, "renderable", None)
            shown.append(str(item))
            if getattr(item, "title", None):
                shown.append(str(item.title))
            if getattr(item, "subtitle", None):
                shown.append(str(item.subtitle))
            if renderable is not None:
                shown.append(str(getattr(renderable, "markup", renderable)))
    monkeypatch.setattr(cli.console, "print", capture)
    return cli, shown, prompts


def test_cli_labels_a_reply_offered_as_a_plan(monkeypatch):
    cli, shown, prompts = _cli_prompt(monkeypatch, "d")
    assert cli._make_plan_approval()(QUESTION, source="reply") == ""
    text = "\n".join(shown)
    assert "no plan was presented" in text.lower()
    assert "nothing has been run yet" in text.lower()
    assert "reply" in prompts[0].lower(), "declining is offered as a way to answer or revise"


def test_cli_suggests_asking_before_each_edit_for_a_reply_with_no_steps(monkeypatch):
    cli, shown, _prompts = _cli_prompt(monkeypatch, "e")
    assert cli._make_plan_approval()(SUMMARY, source="reply") == "readonly"
    assert any("ask before each edit" in line.lower() and "safer" in line.lower() for line in shown)


def test_cli_present_plan_prompt_is_unchanged(monkeypatch):
    cli, shown, prompts = _cli_prompt(monkeypatch, "a")
    assert cli._make_plan_approval()("1. Do it.") == "full-auto"
    assert not any("no plan was presented" in line.lower() for line in shown)
    assert "keep planning" in prompts[0]


def test_cli_after_turn_notice_is_not_repeated_once_a_reply_was_offered(monkeypatch):
    from agent8088 import cli
    shown = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: shown.extend(str(x) for x in a))
    monkeypatch.setattr(cli.A, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(cli.A, "_plan_tool_ran", True)
    monkeypatch.setattr(cli.A, "_plan_approved", False)
    cli._after_turn_plan_state()
    assert not any("no plan was approved" in line for line in shown)


# --- the declined reply is not printed twice ------------------------------------------------------------------

def test_the_engine_remembers_a_declined_reply_for_this_turn_only(planning, monkeypatch):
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("", []), raising=False)
    _run(planning, monkeypatch, [QUESTION])
    assert planning.declined_plan_reply() == QUESTION
    planning.reset_turn_counters()
    assert planning.declined_plan_reply() == ""


def test_an_approved_reply_is_not_remembered_as_declined(planning, monkeypatch):
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", []), raising=False)
    _run(planning, monkeypatch, [SUMMARY, "Done."])
    assert planning.declined_plan_reply() == ""


@pytest.mark.parametrize("answer, expected", [
    (QUESTION, ""),                                              # the panel already showed it
    (QUESTION + "\n\nNot completed: x", "Not completed: x"),     # notes the panel did not show still appear
    ("A different answer.", "A different answer."),               # anything else is shown as usual
])
def test_cli_does_not_print_a_declined_reply_again(monkeypatch, answer, expected):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "declined_plan_reply", lambda: QUESTION)
    assert cli._answer_left_to_show(answer) == expected


def test_cli_shows_the_whole_answer_when_nothing_was_declined(monkeypatch):
    from agent8088 import cli
    monkeypatch.setattr(cli.A, "declined_plan_reply", lambda: "")
    assert cli._answer_left_to_show(QUESTION) == QUESTION


# --- an approved reply must lead to work, not another question ----------------------------------------------

def test_an_answer_with_no_work_after_approval_is_sent_back_once(planning, monkeypatch):
    """Seen live: the approved reply was a question, and the model asked it again instead of choosing and working.
    The plan session then closed with nothing built. It is told once to carry out what the user approved."""
    seen = []
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", []), raising=False)
    answer, calls, _messages, trace = _run(
        planning, monkeypatch, [QUESTION, "I still need to know which one you prefer.", "Done."], seen=seen)
    assert len(calls) >= 3
    push = seen[2][-1]["content"]
    assert "approved" in push.lower() and "carry out" in push.lower()
    assert [s["type"] for s in trace].count("approved_plan_not_started") == 1


def test_the_push_after_approval_happens_only_once(planning, monkeypatch):
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", []), raising=False)
    answer, calls, _messages, trace = _run(
        planning, monkeypatch, [QUESTION, "Which one?", "Which one, really?"])
    assert [s["type"] for s in trace].count("approved_plan_not_started") == 1, "one push, never a loop"
    assert "Which one, really?" in answer


def test_no_push_when_the_approved_plan_did_run_tools(planning, monkeypatch):
    monkeypatch.setattr(planning, "_plan_on_approval", _approver("full-auto", []), raising=False)
    _answer, _calls, _messages, trace = _run(
        planning, monkeypatch, [SUMMARY, TOOL_CALL, "Done, 1+1 is 2."])
    assert not any(s["type"] == "approved_plan_not_started" for s in trace)
