"""/hold, /continue and /finish: stop executing, release, and wind up with a verdict.

Seen in a teammate's run: after "update on step 7. don't execute anything further" the agent kept calling tools, because the
instruction was only prose and every tool stayed available. A held session offers no tools at all, so nothing can run, and /finish
asks for a final answer with the harness's own verification notes."""
from types import SimpleNamespace

import pytest

from agent8088 import cli
from agent8088.trajectory import TrajectoryState


class _Reply:
    def __init__(self, text):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")]
        self.usage = None


@pytest.fixture
def model(monkeypatch, engine):
    """A model that records the tools it was offered and answers with fixed text."""
    seen = {"tools": [], "messages": []}

    def fake(messages, tools, **kw):
        seen["tools"].append(tools)
        seen["messages"].append(messages)
        return _Reply("Step 7 is half done: the arcs are written, the title overlay is not.")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", fake)
    return seen


def _changed_state():
    state = TrajectoryState({}, goal="Add storm arcs to the globe", message_count=1)
    operation = state.before_tool("write_file")
    state.after_tool(operation, "ok", failed=False, blocked=False, mutated=True, verdict="unknown", path="C:\\w\\globe.js")
    return state.snapshot()


# --- the engine call -------------------------------------------------------------------------------

def test_a_held_reply_offers_no_tools(engine, model):
    messages = [{"role": "user", "content": "update on step 7. don't execute anything further"}]
    answer = engine.reply_without_tools(messages, kind="hold", trajectory_state=_changed_state())
    assert model["tools"] == [[]]                       # not one tool was offered
    assert "Step 7 is half done" in answer
    assert "Finished early" not in answer               # a hold adds no end-of-run notes
    assert messages[-1] == {"role": "assistant", "content": answer}
    assert messages[0]["content"].startswith("update on step 7")   # the user's own message is not rewritten


def test_the_hold_instruction_is_sent_to_the_model_but_not_kept(engine, model):
    messages = [{"role": "user", "content": "where are we?"}]
    engine.reply_without_tools(messages, kind="hold")
    assert "no tools will run" in model["messages"][0][-1]["content"]
    assert all("on hold" not in str(m.get("content")) for m in messages)


def test_finish_adds_the_harness_notes(engine, model):
    messages = [{"role": "user", "content": "build it"}, {"role": "assistant", "content": "working"}]
    answer = engine.reply_without_tools(messages, kind="finish", trajectory_state=_changed_state(), record_ask="/finish")
    assert model["tools"] == [[]]
    assert "Finished early at your request" in answer
    assert "Verification" in answer                     # the verification level or note comes from the harness, not the model
    assert [m["role"] for m in messages][-2:] == ["user", "assistant"] and messages[-2]["content"] == "/finish"


def test_finish_after_an_unanswered_user_message_adds_no_second_user_turn(engine, model):
    messages = [{"role": "user", "content": "build it"}]
    engine.reply_without_tools(messages, kind="finish", record_ask="/finish")
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_a_finish_does_not_change_the_saved_state(engine, model):
    state = _changed_state()
    import copy
    before = copy.deepcopy(state)
    engine.reply_without_tools([{"role": "user", "content": "x"}], kind="finish", trajectory_state=state)
    assert state == before


def test_a_system_prompt_leak_is_still_guarded(engine, monkeypatch):
    monkeypatch.setattr(engine, "_create_completion_with_fallback", lambda *a, **k: _Reply("x"))
    monkeypatch.setattr(engine, "_is_system_leak", lambda text: True)
    answer = engine.reply_without_tools([{"role": "user", "content": "x"}], kind="hold")
    assert "can't share my internal system instructions" in answer


# --- the commands ----------------------------------------------------------------------------------

@pytest.fixture
def session(monkeypatch):
    s = SimpleNamespace(hold=False, messages=[], trajectory_state={}, temperature=0.1)
    monkeypatch.setattr(cli, "S", s)
    monkeypatch.setattr(cli, "_save_session_safely", lambda: True)
    return s


def _out(fn, *args):
    with cli.console.capture() as capture:
        fn(*args)
    return capture.get()


def test_hold_and_continue_toggle_the_flag(session):
    assert "on hold" in _out(cli.cmd_hold, "")
    assert session.hold is True
    assert "already on hold" in _out(cli.cmd_hold, "")
    assert "resumed" in _out(cli.cmd_continue, "")
    assert session.hold is False
    assert "not on hold" in _out(cli.cmd_continue, "")


def test_resume_without_a_name_releases_a_hold(session):
    session.hold = True
    assert "resumed" in _out(cli.cmd_resume, "")
    assert session.hold is False


def test_resume_without_a_name_and_no_hold_still_shows_the_usage(session):
    assert "usage" in _out(cli.cmd_resume, "")


def test_finish_with_nothing_to_finish_makes_no_model_call(session, monkeypatch):
    monkeypatch.setattr(cli.A, "reply_without_tools", lambda *a, **k: pytest.fail("no model call expected"))
    assert "nothing to finish" in _out(cli.cmd_finish, "")


def test_finish_ends_the_hold_and_shows_the_answer(session, monkeypatch):
    session.hold = True
    session.messages = [{"role": "user", "content": "build it"}]
    monkeypatch.setattr(cli.A, "reply_without_tools", lambda *a, **k: "Final: arcs done. Verification: unchecked.")
    out = _out(cli.cmd_finish, "")
    assert "Final: arcs done" in out
    assert session.hold is False


def test_a_message_while_held_never_reaches_the_tool_loop(session, monkeypatch):
    session.hold = True
    for name in ("_report_pending_capture", "_ensure_autosave_session", "_warn_pasted_credential"):
        monkeypatch.setattr(cli, name, lambda *a, **k: None)
    monkeypatch.setattr(cli.A, "run_agent", lambda *a, **k: pytest.fail("the tool loop must not run while held"))
    monkeypatch.setattr(cli.A, "reply_without_tools", lambda messages, **k: "Held answer.")
    out = _out(cli.do_chat, "update on step 7. don't execute anything further")
    assert "Held answer." in out and "on hold" in out
    assert session.messages[0] == {"role": "user", "content": "update on step 7. don't execute anything further"}


def test_the_commands_are_registered_and_documented():
    names = {c[0] for c in cli.COMMAND_SPECS}
    for name in ("hold", "continue", "finish"):
        assert name in names and name in cli.COMMAND_DETAILS and name in cli.COMMANDS
    assert cli.COMMAND_DETAILS["hold"] and "/finish" in cli.COMMAND_DETAILS["finish"]
