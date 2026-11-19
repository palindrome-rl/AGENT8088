"""ESC interrupts a turn on Windows too, where there is no termios.

QA sheet (Windows CLI): "Esc to interrupt option doesn't work". The listener
only knew termios, so on Windows it never started while the status line still
said "esc to interrupt".
"""

import io
import threading
import time

import pytest
from rich.console import Console

from agent8088 import cli


class _Console:
    """msvcrt stand-in: the console's key buffer, handed out a byte at a time the
    way getch() hands out the two halves of an arrow or function key."""

    def __init__(self):
        self.keys = []
        self.lock = threading.Lock()
        self.blocked = False

    def type(self, *keys):
        with self.lock:
            for key in keys:
                self.keys += [key[i:i + 1] for i in range(len(key))]

    def kbhit(self):
        with self.lock:
            return bool(self.keys)

    def getch(self):
        with self.lock:
            if not self.keys:
                self.blocked = True  # the real getch() would wait for a key here
                return b""
            return self.keys.pop(0)


@pytest.fixture
def con(monkeypatch):
    console = _Console()
    monkeypatch.setattr(cli, "termios", None)
    monkeypatch.setattr(cli, "msvcrt", console)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    return console


def _settle(con):
    """Give the watcher time to read everything typed so far."""
    deadline = time.monotonic() + 2
    while con.kbhit() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)


def test_esc_sets_the_interrupt(con):
    with cli.EscListener() as esc:
        con.type(b"\x1b")
        assert esc.triggered.wait(2)


def test_special_keys_are_never_taken_for_esc(con):
    with cli.EscListener() as esc:
        # Up, F1, Alt+] (whose second byte is ESC), a letter, and a lone b"\xe0"
        # typed as a character: none of them is ESC, and none may block getch().
        con.type(b"\xe0H", b"\x00;", b"\x00\x1b", b"a", b"\xe0")
        _settle(con)
        assert not esc.triggered.is_set()
        assert not con.blocked
        con.type(b"\x1b")
        assert esc.triggered.wait(2)


def test_turn_end_stops_watching_so_the_next_prompt_gets_its_keys(con):
    with cli.EscListener() as esc:
        watcher = esc._thread
        assert watcher.is_alive()
    assert not watcher.is_alive()
    con.type(b"x")
    time.sleep(0.1)
    assert con.keys == [b"x"]


def test_a_prompt_inside_the_turn_gets_its_keys_and_esc_works_after(con):
    with cli.EscListener() as esc:
        with esc.paused():
            con.type(b"y")
            time.sleep(0.1)
            assert con.keys == [b"y"]
            con.keys.clear()  # the approval prompt read it
        con.type(b"\x1b")
        assert esc.triggered.wait(2)


def _chat(monkeypatch, run_agent):
    out = io.StringIO()
    hints = []

    class _Status(cli._StatusLine):
        def __init__(self, *args, interruptible):
            hints.append(interruptible)
            super().__init__(*args, interruptible=interruptible)

    monkeypatch.setattr(cli, "console", Console(file=out, force_terminal=False, width=200))
    monkeypatch.setattr(cli, "S", cli.Session())
    monkeypatch.setattr(cli, "_StatusLine", _Status)
    monkeypatch.setattr(cli.A, "run_agent", run_agent)
    monkeypatch.setattr(cli, "_await_memory_capture", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_after_turn_plan_state", lambda: None)
    cli.do_chat("hello")
    return out.getvalue(), hints


def test_esc_reaches_the_engine_and_ends_the_turn(con, monkeypatch):
    def run_agent(messages, interrupt_check, **kwargs):
        con.type(b"\x1b")
        deadline = time.monotonic() + 2
        while not interrupt_check():  # what the engine polls between steps
            assert time.monotonic() < deadline, "ESC never reached the engine"
            time.sleep(0.01)
        raise cli.A.AgentInterrupted()

    out, hints = _chat(monkeypatch, run_agent)
    assert "interrupted" in out
    assert hints and all(hints)


def test_no_esc_hint_when_nothing_is_listening(con, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    _, hints = _chat(monkeypatch, lambda messages, **kwargs: "done")
    assert hints and not any(hints)


def test_no_stdin_at_all_is_not_a_crash(con, monkeypatch):
    # pythonw or a detached --prompt-file run: there is nothing to listen to.
    monkeypatch.setattr(cli.sys, "stdin", None)
    assert not cli.EscListener()._active
