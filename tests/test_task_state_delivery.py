"""The controller's task state can reach the model in the conversation, once, when it changes, instead of in the system prompt on
every turn (task_state_delivery=history). The point: every model request then starts with the whole previous request, which is
what lets a server's prompt cache reuse it. The default (`system`) is unchanged."""
import json

import pytest

from agent8088 import trajectory as T


def _state(*ops, goal="Fix it"):
    state = T.TrajectoryState(None, goal, 1)
    for tool, status in ops:
        op = state.before_tool(tool)
        op["status"] = status
    return state


# ----------------------------------------------------------------------------- the state helpers

def test_note_is_empty_before_any_tool_ran():
    assert T.TrajectoryState(None, "goal", 1).state_note() == ""


def test_note_keeps_the_nudges_of_the_system_prompt_block_word_for_word():
    state = _state(("write_file", "done"))
    state.data["verification"] = "pending"
    state.data["untested_changes"] = ["app.py"]
    state.data["stalled"] = True
    note, block = state.state_note(), state.prompt()
    for line in ("Progress is stalled.", "Verification is pending:", "Changed with no tests yet: app.py."):
        assert line in note and line in block


def test_goal_and_workspace_only_in_the_full_note():
    state = _state(("read_text", "done"))
    state.data["workspace"] = "/w"
    assert "Goal: Fix it" in state.state_note(full=True)
    assert "Workspace:" in state.state_note(full=True)
    assert "Goal:" not in state.state_note() and "Workspace:" not in state.state_note()


def test_signature_ignores_ordinary_progress_and_follows_what_matters():
    state = _state(("read_text", "done"))
    base = state.state_signature()
    state.before_tool("read_text")["status"] = "done"        # one more successful read: nothing to tell the model
    assert state.state_signature() == base
    state.data["verification"] = "pending"
    assert state.state_signature() != base
    pending = state.state_signature()
    state.data["untested_changes"] = ["a.py"]
    assert state.state_signature() != pending


def test_a_tool_that_failed_and_then_succeeded_is_no_longer_unresolved():
    state = _state(("read_text", "failed"))
    assert "Unresolved tool work: read_text." in state.state_note()
    state.before_tool("read_text")["status"] = "done"
    assert "Unresolved" not in state.state_note()


# ----------------------------------------------------------------------------- when the loop adds a note

def _tool_result(text="ok"):
    return {"role": "user", "content": f"Tool result (write_file):\n{text}"}


def _history(engine, state):
    notes = engine._TaskStateNotes(refresh=3, limit=5)
    messages = [{"role": "user", "content": "build it"}, {"role": "assistant", "content": "calling"}, _tool_result()]
    return notes, messages


def test_first_note_is_full_and_goes_on_the_newest_tool_result(engine):
    state = _state(("write_file", "done"))
    notes, messages = _history(engine, state)
    assert notes.deliver(messages, state)
    assert messages[-1]["content"].startswith("Tool result (write_file):\nok\n[agent8088] Task state")
    assert "Goal: Fix it" in messages[-1]["content"]
    assert messages[0]["content"] == "build it" and messages[1]["content"] == "calling"   # nothing earlier is touched


def test_nothing_is_added_while_nothing_changed(engine):
    state = _state(("write_file", "done"))
    notes, messages = _history(engine, state)
    notes.deliver(messages, state)
    for _ in range(6):
        messages += [{"role": "assistant", "content": "x"}, _tool_result()]
        assert not notes.deliver(messages, state)     # nothing open, nothing changed, a note is still in the conversation


def test_a_change_adds_a_short_note_without_the_goal(engine):
    state = _state(("write_file", "done"))
    notes, messages = _history(engine, state)
    notes.deliver(messages, state)
    messages += [{"role": "assistant", "content": "x"}, _tool_result()]
    state.data["verification"] = "pending"
    assert notes.deliver(messages, state)
    assert "Verification is pending" in messages[-1]["content"] and "Goal:" not in messages[-1]["content"]


def test_an_open_item_is_repeated_every_few_turns_and_only_then(engine):
    state = _state(("write_file", "done"))
    state.data["verification"] = "pending"
    notes, messages = _history(engine, state)
    notes.deliver(messages, state)
    added = []
    for _ in range(7):
        messages += [{"role": "assistant", "content": "x"}, _tool_result()]
        added.append(notes.deliver(messages, state))
    assert added == [False, False, True, False, False, True, False]     # refresh=3


def test_a_note_is_added_again_after_compaction_dropped_the_notes(engine):
    state = _state(("write_file", "done"))
    notes, messages = _history(engine, state)
    notes.deliver(messages, state)
    messages[:] = [{"role": "system", "content": "Conversation summary:\nsomething"}, {"role": "user", "content": "build it"},
                   {"role": "assistant", "content": "x"}, _tool_result("later")]
    assert notes.deliver(messages, state)
    assert "Goal: Fix it" in messages[-1]["content"]      # restated in full


def test_a_new_request_in_the_same_conversation_starts_with_a_full_note(engine):
    state = _state(("write_file", "done"))
    notes, messages = _history(engine, state)
    notes.deliver(messages, state)
    messages += [{"role": "assistant", "content": "done"}, {"role": "user", "content": "now something else"},
                 {"role": "assistant", "content": "x"}, _tool_result("new work")]
    assert notes.deliver(messages, state)
    assert "Goal: Fix it" in messages[-1]["content"]


def test_a_message_the_person_wrote_is_never_changed(engine):
    state = _state(("write_file", "done"))
    notes = engine._TaskStateNotes()
    messages = [{"role": "user", "content": "a new request"}]
    assert not notes.deliver(messages, state)
    assert messages[0]["content"] == "a new request"


def test_a_message_with_images_is_left_alone_and_the_note_waits(engine):
    state = _state(("write_file", "done"))
    notes = engine._TaskStateNotes()
    parts = [{"type": "text", "text": "Tool result (view_image):"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
    messages = [{"role": "assistant", "content": "x"}, {"role": "user", "content": parts}]
    assert not notes.deliver(messages, state)
    assert messages[-1]["content"] is parts
    messages += [{"role": "assistant", "content": "y"}, _tool_result()]
    assert notes.deliver(messages, state)                  # the next plain tool result carries it


def test_the_number_of_notes_is_capped(engine):
    state = _state(("write_file", "done"))
    notes = engine._TaskStateNotes(refresh=1, limit=3)
    messages = [{"role": "user", "content": "go"}]
    sent = 0
    for i in range(10):
        messages += [{"role": "assistant", "content": "x"}, _tool_result()]
        state.data["untested_changes"] = [f"f{i}.py"]       # a change every round
        sent += notes.deliver(messages, state)
    assert sent == 3


def test_the_note_is_not_mistaken_for_a_message_from_the_person(engine):
    state = _state(("write_file", "done"))
    notes, messages = _history(engine, state)
    notes.deliver(messages, state)
    assert engine._genuine_user_turns(messages) == [messages[0]]


# ----------------------------------------------------------------------------- the whole loop, stub model

def _call(name, args):
    call = type("T", (), {"function": type("F", (), {"name": name, "arguments": json.dumps(args)})()})()
    message = type("M", (), {"content": "", "tool_calls": [call]})()
    return type("R", (), {"choices": [type("C", (), {"message": message, "finish_reason": "tool_calls"})()]})()


def _text(content):
    return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})(),
                                                     "finish_reason": "stop"})()]})()


def _run_loop(engine, monkeypatch, delivery, calls=7):
    """Run the real loop with a stub model that writes a file each round; return what each request would carry."""
    monkeypatch.setattr(engine, "TASK_STATE_DELIVERY", delivery)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    requests = []

    def model(messages, tools, **kwargs):
        requests.append(engine._normalised_messages(kwargs.get("system_prompt") or "",
                                                    json.loads(json.dumps(messages, default=str))))
        n = len(requests)
        if n <= calls:
            return _call("write_file", {"filename": f"mod{n}.py", "content": f"x = {n}\n"})
        return _text("done")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", model)
    monkeypatch.setattr(engine, "run_tool", lambda name, args, **kw: "Wrote the file")
    engine.run_agent([{"role": "user", "content": "write some modules"}], max_turns=calls + 6, dynamic_turns=False)
    return requests


def _extends(previous, current):
    return current[:len(previous)] == previous


def test_in_history_mode_every_request_starts_with_the_whole_previous_one(engine, monkeypatch):
    requests = _run_loop(engine, monkeypatch, "history")
    assert len(requests) >= 6
    for i in range(1, len(requests)):
        assert _extends(requests[i - 1], requests[i]), f"request {i} does not start with request {i - 1}"
    assert any("[agent8088] Task state" in str(m.get("content")) for m in requests[-1])
    assert all("## Current task state" not in str(requests[i][0]["content"]) for i in range(len(requests)))
    assert len({r[0]["content"] for r in requests}) == 1       # the system prompt never changes

    assert any("Notes like this one come from the harness" in str(m.get("content")) for r in requests for m in r)


def test_until_the_first_note_a_request_is_exactly_what_it_was_before(engine, monkeypatch):
    """History mode adds nothing to the system prompt, so the model sees the same first request in both modes."""
    history = _run_loop(engine, monkeypatch, "history", calls=3)
    system = _run_loop(engine, monkeypatch, "system", calls=3)
    assert history[0] == system[0]


def test_in_system_mode_the_system_prompt_still_carries_the_state_and_changes(engine, monkeypatch):
    requests = _run_loop(engine, monkeypatch, "system")
    systems = [r[0]["content"] for r in requests]
    assert any("## Current task state" in s for s in systems)
    assert len(set(systems)) > 1                           # the old behaviour, kept as the default
    assert not any("[agent8088] Task state" in str(m.get("content")) for r in requests for m in r)
    # and the property the history mode adds does fail here, so the test above can tell the two apart
    assert not all(_extends(requests[i - 1], requests[i]) for i in range(1, len(requests)))


def test_default_is_the_old_behaviour(engine):
    assert engine.TASK_STATE_DELIVERY == "system"
