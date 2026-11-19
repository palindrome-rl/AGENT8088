"""A plain-language request to pause and report is answered in text, with no tool on offer.

Seen in use: asked to pause execution and send an update, the agent kept executing, because the request was only prose and every
tool stayed available. /hold already fixes that with a slash command; this covers the same request written in words. It is
deliberately narrow, and it is one message only: the next message carries on normally, which is the "carry on" part."""

import io
import threading
from contextlib import nullcontext

import pytest
from rich.console import Console

from agent8088 import cli


@pytest.mark.parametrize("text", [
    "pause",
    "Pause.",
    "hold on",
    "stop",
    "wait",
    "pause and send me an update",
    "Please stop executing and tell me where you are",
    "update on step 7. don't execute anything further",
    "Stop what you are doing and give me a status update",
    "Can you pause for a moment?",
    "hold on, what have you done so far?",
    "don't execute anything further, just report progress",
    "Do not run anything more until I reply, but tell me where you are.",
    "don't continue, send me a progress update",
    "halt and summarize what is done",
    "Pause execution, send me an update, then we will decide.",
])
def test_a_request_to_pause_and_report_is_recognised(text):
    assert cli._is_pause_request(text)


@pytest.mark.parametrize("text", [
    "",
    "continue",
    "build the app and report the status when done",
    "stop using requests and use urllib instead",
    "wait for the server to start, then report the status of the endpoints",
    "pause the video when the user presses space",
    "pause the music player's autoplay and add a mute button",
    "Do not change the prompt files. Update the README with the new flags and report what you changed.",
    "stop the server on port 8000 and start it on 8001",
    "Do not run the tests, just update the README and report what you changed.",
    "Don't change the sprites folder. Update the data and report back.",
    "/hold",
    "please write a status page for the app that shows progress for each job and lets users pause a job " * 3,
])
def test_a_task_that_only_mentions_those_words_is_not_a_pause(text):
    assert not cli._is_pause_request(text)


@pytest.fixture
def out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, force_terminal=False, width=200))
    return buf


@pytest.fixture
def session(monkeypatch):
    fresh = cli.Session()
    monkeypatch.setattr(cli, "S", fresh)
    monkeypatch.setattr(cli, "_ensure_autosave_session", lambda query: None)
    monkeypatch.setattr(cli, "_report_pending_capture", lambda: None)
    monkeypatch.setattr(cli, "_save_session_safely", lambda: None)
    return fresh


class _Esc:
    _active = False

    def __init__(self):
        self.triggered = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def paused(self):
        return nullcontext()


@pytest.fixture
def calls(monkeypatch):
    seen = {"run_agent": 0, "reply": []}

    def run_agent(messages, **kwargs):
        seen["run_agent"] += 1
        return "worked"

    def reply_without_tools(messages, *, kind, **kwargs):
        seen["reply"].append(kind)
        answer = "Step 7 is half done."
        messages.append({"role": "assistant", "content": answer})
        return answer

    monkeypatch.setattr(cli, "EscListener", _Esc)
    monkeypatch.setattr(cli.A, "run_agent", run_agent)
    monkeypatch.setattr(cli.A, "reply_without_tools", reply_without_tools)
    monkeypatch.setattr(cli, "_await_memory_capture", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_after_turn_plan_state", lambda: None)
    return seen


def test_a_pause_request_gets_a_text_only_answer_and_runs_nothing(out, session, calls):
    cli.do_chat("pause and send me an update")
    assert calls == {"run_agent": 0, "reply": ["hold"]}
    assert "Step 7 is half done." in out.getvalue()
    assert "paused: no tools ran" in out.getvalue()


def test_the_pause_is_for_one_message_and_the_next_one_carries_on(out, session, calls):
    cli.do_chat("pause and send me an update")
    assert session.hold is False
    cli.do_chat("ok, carry on")
    assert calls["run_agent"] == 1 and calls["reply"] == ["hold"]


def test_an_ordinary_message_still_runs_the_agent(out, session, calls):
    cli.do_chat("build the app and report the status when done")
    assert calls == {"run_agent": 1, "reply": []}


def test_a_session_already_on_hold_still_answers_in_text_and_stays_on_hold(out, session, calls):
    session.hold = True
    cli.do_chat("any news?")
    assert calls == {"run_agent": 0, "reply": ["hold"]}
    assert session.hold is True
    assert "on hold: no tools ran" in out.getvalue()


@pytest.mark.parametrize('text', [
    'Implement play and pause buttons.',
    'Add controls: pause and report progress.',
    'Pause playback when the user presses space.',
    'Freeze animation when the mouse leaves the canvas.',
    'Show the text: pause and send me an update.',
])
def test_ui_and_quoted_instruction_tasks_do_not_pause_the_agent(text):
    assert not cli._is_pause_request(text)


@pytest.mark.parametrize('text', [
    'Pause your current work and give me an update.',
    'Pause the execution and report progress.',
])
def test_explicit_agent_work_can_be_named_in_a_pause(text):
    assert cli._is_pause_request(text)


def test_pause_bypasses_pasted_attachment_detection(monkeypatch, out, session, calls):
    lines = iter(['pause and send an update about notes.txt', '/exit'])
    monkeypatch.setattr(cli, '_read_line', lambda: next(lines))
    monkeypatch.setattr(cli, '_detect_pasted_file', lambda *_a: pytest.fail('pause inspected a file'))
    cli._repl_turns()
    assert calls == {'run_agent': 0, 'reply': ['hold']}


def test_headless_pause_records_its_own_answer_instead_of_previous_failure(out, session, calls):
    session.record_end_reason = True
    session.turns_this_run = 12
    session.last_trace = [{'type': 'model_error', 'detail': 'previous turn'}]
    session.conversation_trace = [{'interrupted': True, 'stop_reason': 'SIGTERM'}]
    cli.do_chat('pause and send me an update')
    assert cli._run_end_reason(session.last_trace) == 'answered'
    assert session.last_trace[-1]['paused'] is True
    assert session.conversation_trace[-1]['input'] == 'pause and send me an update'
    assert session.turns_this_run == 0
