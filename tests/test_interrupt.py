"""ESC/Ctrl+C handling and the permission-approval picker.

Two keys, two meanings: Ctrl+C ends agent8088, ESC aborts only the task in
flight. The approval prompt used to conflate them — it caught KeyboardInterrupt
and turned it into a plain "deny", so the turn carried on and there was no way
out of a permission prompt at all.

The prompt also ran while EscListener held stdin in cbreak mode with a reader
thread discarding bytes, which ate the keystrokes the prompt was waiting for.
_handle_escalation now hands the terminal back for the duration.
"""
import io

import pytest
from rich.console import Console

from agent8088 import cli
from agent8088 import engine as A


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    monkeypatch.setattr(cli, "console", Console(file=io.StringIO(), width=100,
                                                color_system=None))


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(cli, "_session_allowlist", set())
    monkeypatch.setattr(A, "PERMISSION_MODE", "readonly")


# \x1f-delimited: a Windows path (C:\\Users\\...) splits on ':' and corrupts
# the parse, so the payload must be built the way request_escalation builds it or
# _handle_escalation will not recognise it at all.
ESCALATION = ("ESCALATION_REQUEST\x1fedit\x1fnew_file\x1f/tmp/x\x1f"
              "Tool 'write_file' requires write_text access")


def _choose(monkeypatch, value):
    """Force the picker to return `value` (None simulates ESC)."""
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return value

    monkeypatch.setattr(cli, "_permission_choice", fake)
    return calls


# --- EscListener.paused() -------------------------------------------------

def test_paused_is_a_noop_when_listener_inactive():
    """Non-tty stdin (tests, pipes) must behave exactly as before."""
    listener = cli.EscListener()
    listener._active = False

    with listener.paused():
        pass  # must not raise, must not touch termios

    assert listener._thread is None


def test_paused_preserves_an_already_triggered_esc():
    """ESC pressed just before the prompt still aborts the turn."""
    listener = cli.EscListener()
    listener._active = False
    listener.triggered.set()

    with listener.paused():
        pass

    assert listener.triggered.is_set()


def test_handle_escalation_pauses_the_listener_around_the_prompt():
    """The reader thread must not be competing for stdin while we prompt."""
    events = []

    class _Listener:
        def paused(self):
            import contextlib

            @contextlib.contextmanager
            def _cm():
                events.append("paused")
                yield
                events.append("resumed")

            return _cm()

    def fake_choice(*args, **kwargs):
        events.append("prompted")
        return "deny"

    original = cli._permission_choice
    cli._permission_choice = fake_choice
    try:
        cli._handle_escalation(ESCALATION, esc=_Listener())
    finally:
        cli._permission_choice = original

    assert events == ["paused", "prompted", "resumed"]


# --- Ctrl+C ends the program --------------------------------------------

def test_ctrl_c_at_approval_prompt_propagates(monkeypatch):
    """Ctrl+C must escape the prompt, not be swallowed into a silent deny."""
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_permission_choice", interrupt)

    with pytest.raises(KeyboardInterrupt):
        cli._handle_escalation(ESCALATION)


def test_closed_stdin_still_denies(monkeypatch):
    """EOF is not a user decision — fail closed, but don't kill the process."""
    monkeypatch.setattr(cli, "_permission_choice",
                        lambda *a, **k: (_ for _ in ()).throw(EOFError))

    assert cli._handle_escalation(ESCALATION) is False


# --- ESC aborts the task -------------------------------------------------

def test_esc_at_approval_prompt_aborts_the_turn(monkeypatch):
    """The picker returns None on ESC; that ends the task, not the program."""
    _choose(monkeypatch, None)

    with pytest.raises(A.AgentInterrupted):
        cli._handle_escalation(ESCALATION)


# --- approval logic is unchanged ----------------------------------------

def test_once_grants_a_single_escalation(monkeypatch):
    granted = []
    monkeypatch.setattr(A, "grant_escalation", lambda ct="": granted.append(ct))
    _choose(monkeypatch, "once")

    assert cli._handle_escalation(ESCALATION) is True
    assert granted == ["new_file"]
    assert cli._session_allowlist == set()


def test_session_adds_to_the_allowlist(monkeypatch):
    monkeypatch.setattr(A, "grant_escalation", lambda ct="": None)
    _choose(monkeypatch, "session")

    assert cli._handle_escalation(ESCALATION) is True
    assert "new_file" in cli._session_allowlist


def test_deny_returns_false(monkeypatch):
    granted = []
    monkeypatch.setattr(A, "grant_escalation", lambda ct="": granted.append(ct))
    _choose(monkeypatch, "deny")

    assert cli._handle_escalation(ESCALATION) is False
    assert granted == []


def test_session_allowlist_short_circuits_the_prompt(monkeypatch):
    """A change type already approved for the session must not re-prompt."""
    monkeypatch.setattr(A, "grant_escalation", lambda ct="": None)
    cli._session_allowlist.add("new_file")
    calls = _choose(monkeypatch, "deny")

    assert cli._handle_escalation(ESCALATION) is True
    assert calls == []


def test_plan_only_mode_uses_the_plan_prompt(monkeypatch):
    monkeypatch.setattr(A, "PERMISSION_MODE", "plan-only")
    monkeypatch.setattr(A, "grant_escalation", lambda ct="": None)
    calls = _choose(monkeypatch, "approve")

    assert cli._handle_escalation(ESCALATION) is True
    values = [value for value, _label in calls[0][0][1]]
    assert values == ["approve", "deny"]


def test_plan_only_deny_returns_false(monkeypatch):
    monkeypatch.setattr(A, "PERMISSION_MODE", "plan-only")
    _choose(monkeypatch, "deny")

    assert cli._handle_escalation(ESCALATION) is False


def test_readonly_mode_offers_once_session_deny(monkeypatch):
    monkeypatch.setattr(A, "grant_escalation", lambda ct="": None)
    calls = _choose(monkeypatch, "once")

    cli._handle_escalation(ESCALATION)

    values = [value for value, _label in calls[0][0][1]]
    assert values == ["once", "session", "deny"]


# --- _permission_choice fallback ----------------------------------------

OPTIONS = [("once", "Once"), ("session", "Session"), ("deny", "Deny")]
TYPED = {"o": "once", "once": "once", "s": "session", "session": "session"}


def _typed(monkeypatch, response):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli.console, "input", lambda _prompt: response)


@pytest.mark.parametrize("typed,expected", [
    ("o", "once"),
    ("once", "once"),
    ("s", "session"),
    ("session", "session"),
    ("d", "deny"),
    ("", "deny"),
    ("garbage", "deny"),
])
def test_typed_fallback_maps_legacy_answers(monkeypatch, typed, expected):
    """Non-tty stdin keeps the old typed contract, including deny-by-default."""
    _typed(monkeypatch, typed)

    assert cli._permission_choice("Allow?", OPTIONS, "prompt: ", TYPED,
                                  default="deny") == expected


def test_typed_fallback_does_not_swallow_ctrl_c(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)

    def interrupt(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.console, "input", interrupt)

    with pytest.raises(KeyboardInterrupt):
        cli._permission_choice("Allow?", OPTIONS, "prompt: ", TYPED, default="deny")


# --- the real picker, driven by simulated keystrokes ---------------------

def _drive_picker(monkeypatch, keys):
    """Run _permission_choice's tty branch against a fake terminal.

    The picker only runs on a tty, so without a pipe-driven session the
    InquirerPy call would never be exercised by the suite at all — the very
    place a wrong keybinding would hide.
    """
    pytest.importorskip("InquirerPy")
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        with create_app_session(input=pipe, output=DummyOutput()):
            return cli._permission_choice("Allow this action?", OPTIONS,
                                          "unused: ", TYPED, default="deny")


def test_picker_enter_takes_the_default(monkeypatch):
    """Enter on an untouched picker denies — the safe choice stays the default."""
    assert _drive_picker(monkeypatch, "\r") == "deny"


def test_picker_arrow_keys_select_another_option(monkeypatch):
    assert _drive_picker(monkeypatch, "\x1b[A\r") == "session"


def test_picker_esc_returns_none(monkeypatch):
    """ESC declines to answer, which _handle_escalation turns into an abort."""
    assert _drive_picker(monkeypatch, "\x1b") is None


# --- engine: ESC lands between tool calls --------------------------------

def _tool_call_response(expression):
    content = f'✿FUNCTION✿: calculate ✿ARGS✿: {{"expression": "{expression}"}}'

    class _Usage:
        prompt_tokens = 1
        completion_tokens = 1

    return type("R", (), {
        "usage": _Usage(),
        "choices": [type("C", (), {
            "message": type("M", (), {"content": content})(),
            "finish_reason": "stop",
        })()],
    })()


def test_esc_during_streaming_stops_before_the_tool_runs(monkeypatch, engine):
    """ESC pressed while the model streams must not let the tool fire.

    The loop only checked interrupt_check at the top of each turn, so a tool
    the model had already asked for still executed — the user watched the thing
    they just cancelled go ahead and run.
    """
    interrupted = {"yes": False}
    executed = []

    def _fake_completion(messages, tools, **kwargs):
        interrupted["yes"] = True  # ESC lands while the response streams
        return _tool_call_response("1+1")

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake_completion)
    monkeypatch.setattr(engine, "exec_tool",
                        lambda name, args, **kw: executed.append(name) or "2")

    with pytest.raises(engine.AgentInterrupted):
        engine.run_agent([{"role": "user", "content": "hi"}], max_turns=3,
                         interrupt_check=lambda: interrupted["yes"])

    assert executed == [], "a cancelled turn still executed its tool call"


def test_abort_at_the_approval_prompt_unwinds_the_whole_turn(monkeypatch, engine):
    """ESC at the prompt must escape run_agent, not just deny the one tool.

    _handle_escalation raises AgentInterrupted from inside the on_escalation
    callback, so this pins the path from that callback out through the agent
    loop — the thing that makes ESC feel like "stop" instead of "no".
    """
    monkeypatch.setattr(engine, "_create_completion_with_fallback",
                        lambda *a, **kw: _tool_call_response("1+1"))
    monkeypatch.setattr(engine, "exec_tool",
                        lambda *a, **kw: ESCALATION)

    def _abort(_name, _result):
        raise engine.AgentInterrupted()

    with pytest.raises(engine.AgentInterrupted):
        engine.run_agent([{"role": "user", "content": "hi"}], max_turns=3,
                         on_escalation=_abort)

    assert engine._active_budget is None, "budget leaked past an aborted turn"


def test_uninterrupted_turn_still_runs_its_tools(monkeypatch, engine):
    """The new check must not make every tool call look cancelled."""
    executed = []
    calls = {"n": 0}

    def _fake_completion(messages, tools, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call_response("1+1")
        return type("R", (), {
            "usage": type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})(),
            "choices": [type("C", (), {
                "message": type("M", (), {"content": "done"})(),
                "finish_reason": "stop",
            })()],
        })()

    monkeypatch.setattr(engine, "_create_completion_with_fallback", _fake_completion)
    monkeypatch.setattr(engine, "exec_tool",
                        lambda name, args, **kw: executed.append(name) or "2")

    engine.run_agent([{"role": "user", "content": "hi"}], max_turns=3,
                     interrupt_check=lambda: False)

    assert executed == ["calculate"]
