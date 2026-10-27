"""/hold, /continue and /finish: stop executing, release, and wind up with a verdict.

Seen in a teammate's run: after "update on step 7. don't execute anything further" the agent kept calling tools, because the
instruction was only prose and every tool stayed available. A held session offers no tools at all, so nothing can run, and /finish
asks for a final answer with the harness's own verification notes."""
from types import SimpleNamespace
import asyncio
import copy
import json

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


def test_finish_retains_tested_evidence_after_history_grows(engine, model):
    state = _changed_state()
    state['verification'] = 'verified'
    state['passed_checks'] = 1
    before = copy.deepcopy(state)
    messages = [{'role': 'user', 'content': 'build'}, {'role': 'assistant', 'content': 'built'},
                {'role': 'user', 'content': 'finish'}]
    answer = engine.reply_without_tools(messages, kind='finish', trajectory_state=state)
    assert 'Verification: tested' in answer
    assert state == before


def test_finish_keeps_failed_smoke_subgoals_and_unfinished_files(engine, model):
    state = _changed_state()
    state['verification'] = 'verified'
    task = TrajectoryState(state, '', state['message_count'])
    task.record_smoke('globe.js', 'globe.js: smoke test FAILED - broken control', failed=True)
    task.set_subgoals(['Add the legend'])
    task.note_partial('globe.js', 100)
    before = copy.deepcopy(state)
    messages = [{'role': 'user', 'content': 'build'}, {'role': 'assistant', 'content': 'all done'}]
    answer = engine.reply_without_tools(messages, kind='finish', trajectory_state=state)
    assert answer.startswith('NOT VERIFIED:')
    assert 'broken control' in answer and 'Add the legend' in answer and 'cut off' in answer
    assert state == before


def test_finish_never_mirrors_files_or_probes_the_backend(engine, model, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(engine, 'ARTIFACTS_ROOT', tmp_path / 'artifacts')
    monkeypatch.setattr(engine, '_mirror_redirected_writes', lambda *a: pytest.fail('finish must not copy files'))
    monkeypatch.setattr(engine, '_resolve_sandbox_backend', lambda: pytest.fail('finish must not probe the backend'))
    state = _changed_state()
    target, landed = tmp_path / 'deliverable.js', tmp_path / 'artifacts/deliverable.js'
    state['written_paths'] = [str(target)]
    state['redirected_writes'] = [[str(target), str(landed)]]
    answer = engine.reply_without_tools([{'role': 'user', 'content': 'finish'}], kind='finish', trajectory_state=state)
    assert 'Finished early' in answer and str(landed.parent) in answer
    assert not target.exists()


@pytest.mark.parametrize('name', ['tool', 'task', 'plan', 'raw', 'image', 'doctor', 'search', 'sandbox', 'compact'])
@pytest.mark.parametrize('bare', [False, True])
def test_execution_commands_are_blocked_by_dispatch_while_held(session, monkeypatch, name, bare):
    session.hold = True
    handler = lambda _rest: pytest.fail('execution handler reached during hold')
    monkeypatch.setitem(cli.COMMANDS, name, handler)
    out = _out(cli._run_repl_command, handler, 'anything', '' if bare else name)
    assert 'on hold' in out and session.hold


def test_direct_tool_and_task_calls_are_also_blocked(session, monkeypatch):
    session.hold = True
    monkeypatch.setattr(cli.A, 'exec_tool', lambda *a, **k: pytest.fail('tool ran'))
    assert 'on hold' in _out(cli.cmd_tool, 'execute_shell {"command":"echo bad"}')
    assert 'on hold' in _out(cli.cmd_task, 'start do work')
    assert cli._active_tool_specs() == {}


def test_pasted_files_are_not_read_while_held(session, monkeypatch):
    session.hold = True
    monkeypatch.setattr(cli.A, 'resolve_pasted_path', lambda *a: pytest.fail('file was opened'))
    assert cli._handle_pasted_file('private.txt', 'read this') is False


def test_empty_finish_releases_and_saves_the_hold(session):
    session.hold = True
    assert 'nothing to finish' in _out(cli.cmd_finish, '')
    assert session.hold is False


def test_hold_persists_across_cli_save_resume_and_continue(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, 'SESSIONS_DIR', tmp_path / 'sessions')
    s = cli.Session()
    s.name = 'held-session'
    s.messages = [{'role': 'user', 'content': 'build'}]
    monkeypatch.setattr(cli, 'S', s)
    _out(cli.cmd_hold, '')
    saved = cli._session_path(s.name)
    assert json.loads(saved.read_text(encoding='utf-8'))['hold'] is True
    monkeypatch.setattr(cli, 'S', cli.Session())
    _out(cli.cmd_resume, 'held-session')
    assert cli.S.hold is True
    _out(cli.cmd_continue, '')
    assert json.loads(saved.read_text(encoding='utf-8'))['hold'] is False


def test_web_sessions_restore_hold_and_new_sessions_do_not_inherit_it(monkeypatch):
    from agent8088 import web_server as web
    s = cli.Session()
    s.hold = True
    monkeypatch.setattr(cli, 'S', s)
    assert web._blank_session(cli).hold is False
    assert web._load_session(cli, 'held', {'messages': [], 'hold': True}).hold is True
    assert web._load_session(cli, 'old', {'messages': []}).hold is False


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


def test_web_commands_cannot_bypass_hold(session, monkeypatch):
    from agent8088 import web_server as web
    session.hold = True
    monkeypatch.setattr(cli.A, 'enter_plan_mode', lambda: pytest.fail('plan mode changed while held'))
    ws = _Socket()
    for command in ('tool', 'task', 'plan', 'raw'):
        asyncio.run(web._handle_command_impl(ws, {'command': command, 'args': 'do work'}, cli.A, cli))
    assert all('On hold' in event['result'] for event in ws.events)


def test_web_held_chat_uses_text_only_path(engine, monkeypatch, tmp_path):
    from agent8088 import web_server as web
    s = cli.Session()
    s.id, s.name, s.hold = 'a' * 32, 'held', True
    monkeypatch.setattr(cli, 'S', s)
    monkeypatch.setattr(cli, '_estimate_context_pct', lambda *a: 0)
    monkeypatch.setattr(web, '_turn_session', lambda *a: s)
    monkeypatch.setattr(web, '_save_session', lambda *a: None)
    monkeypatch.setattr(web, '_record_turn_trace', lambda *a, **k: None)
    monkeypatch.setattr(engine, 'run_agent', lambda *a, **k: pytest.fail('tool loop ran while held'))
    def reply(messages, **kwargs):
        assert kwargs['kind'] == 'hold'
        messages.append({'role': 'assistant', 'content': 'Held answer.'})
        return 'Held answer.'
    monkeypatch.setattr(engine, 'reply_without_tools', reply)
    ws = _Socket()
    asyncio.run(web._handle_chat(ws, {'text': 'status update', 'session_id': s.id}, engine, cli))
    assert any(event.get('type') == 'answer' for event in ws.events), ws.events
    assert not any(event.get('type') in {'error', 'tool_calls', 'tool_result'} for event in ws.events), ws.events
    assert s.hold is True


def test_web_held_chat_does_not_open_new_attachments(session, monkeypatch):
    from agent8088 import web_server as web
    session.hold, session.id = True, 'a' * 32
    monkeypatch.setattr(web, '_turn_session', lambda *a: session)
    monkeypatch.setattr(web, '_validated_attachments', lambda *a: pytest.fail('attachment was opened'))
    ws = _Socket()
    asyncio.run(web._handle_chat(ws, {'text': 'read', 'attachments': ['x']}, cli.A, cli))
    assert ws.events[0]['type'] == 'error' and 'On hold' in ws.events[0]['message']


def test_web_session_writer_preserves_hold(monkeypatch):
    from agent8088 import web_server as web
    s = cli.Session()
    s.name, s.hold = 'held', True
    captured = []
    monkeypatch.setattr(web, '_read_session_file', lambda *a: {})
    monkeypatch.setattr(web, '_save_session_file', lambda path, data: captured.append(data))
    web._save_session(cli, s)
    assert captured[0]['hold'] is True


def test_unexpected_model_tool_calls_are_never_executed(engine, monkeypatch):
    message = SimpleNamespace(content='', tool_calls=[SimpleNamespace(
        function=SimpleNamespace(name='execute_shell', arguments='{"command":"echo bad"}'))])
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    monkeypatch.setattr(engine, '_create_completion_with_fallback', lambda *a, **k: response)
    monkeypatch.setattr(engine, 'exec_tool', lambda *a, **k: pytest.fail('unexpected tool call executed'))
    answer = engine.reply_without_tools([{'role': 'user', 'content': 'status'}], kind='hold')
    assert 'No answer was produced' in answer


def test_continue_never_relaxes_the_permission_mode(session, monkeypatch):
    session.hold = True
    monkeypatch.setattr(cli.A, 'PERMISSION_MODE', 'readonly')
    _out(cli.cmd_continue, '')
    assert session.hold is False and cli.A.PERMISSION_MODE == 'readonly'


@pytest.mark.parametrize('name, args', [
    ('invoke_tool', ('execute_shell', {'command': 'echo bad'})),
    ('run_agent', ('research', {'task': 'do work'})),
    ('start_task', ({'goal': 'do work'},)),
    ('resume_task', ('task-id',)),
])
def test_direct_web_execution_endpoints_respect_hold(session, monkeypatch, name, args):
    from agent8088 import web_server as web
    session.hold = True
    monkeypatch.setattr(web, '_cl', lambda: cli)
    monkeypatch.setattr(web, '_eng', lambda: pytest.fail('execution runtime reached'))
    monkeypatch.setattr(web, '_task_store', lambda: pytest.fail('task store opened'))
    response = asyncio.run(getattr(web, name)(*args))
    assert response.status_code == 409 and b'On hold' in response.body


def test_direct_web_approvals_cannot_execute_during_hold_but_can_be_cancelled(session, monkeypatch):
    from agent8088 import web_server as web
    import time
    session.hold = True
    monkeypatch.setattr(web, '_cl', lambda: cli)
    monkeypatch.setattr(web, '_eng', lambda: pytest.fail('approval execution reached'))
    monkeypatch.setattr(web, '_pending_direct_tools', {'held': {'created': time.time()}})
    response = asyncio.run(web.approve_direct_tool('held', {'approved': True}))
    assert response.status_code == 409 and 'held' in web._pending_direct_tools
    assert asyncio.run(web.approve_direct_tool('held', {'approved': False})) == {'ok': True, 'cancelled': True}
