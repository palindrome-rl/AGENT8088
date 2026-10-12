"""Reasoning stays in the terminal scrollback once its round is over."""

import io

import pytest
from rich.console import Console

from agent8088 import cli


@pytest.fixture
def out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, force_terminal=False, width=200))
    return buf


@pytest.fixture
def session(monkeypatch):
    fresh = cli.Session()
    monkeypatch.setattr(cli, "S", fresh)
    return fresh


class _Esc:
    def __init__(self):
        import threading
        self.triggered = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def paused(self):
        from contextlib import nullcontext
        return nullcontext()


def _chat(monkeypatch, run_agent):
    monkeypatch.setattr(cli, "EscListener", _Esc)
    monkeypatch.setattr(cli.A, "run_agent", run_agent)
    monkeypatch.setattr(cli, "_await_memory_capture", lambda stored: None)
    monkeypatch.setattr(cli, "_after_turn_plan_state", lambda: None)
    cli.do_chat("hello")


def _two_rounds(messages, **kwargs):
    kwargs["spin"]("thinking...")
    kwargs["on_token"]("reasoning", "first round idea")
    kwargs["on_calls"]([{"name": "read_text", "arguments": {"filename": "notes.md"}}])
    kwargs["spin"]("thinking...")
    kwargs["on_token"]("reasoning", "second round idea")
    return "the final answer"


def test_each_rounds_reasoning_is_kept_in_order(monkeypatch, out, session):
    session.show_reasoning = True
    _chat(monkeypatch, _two_rounds)
    text = out.getvalue()

    first, tool, second, answer = (text.find(s) for s in (
        "first round idea", "read_text", "second round idea", "the final answer"))
    assert -1 not in (first, tool, second, answer)
    # Reasoning above the tool rows it led to, and the last round above the answer.
    assert first < tool < second < answer
    assert text.count("first round idea") == 1


def test_hidden_reasoning_is_never_printed(monkeypatch, out, session):
    session.show_reasoning = False
    _chat(monkeypatch, _two_rounds)
    text = out.getvalue()

    assert "round idea" not in text
    assert "the final answer" in text


def test_a_stream_reset_drops_only_the_broken_round(monkeypatch, out, session):
    session.show_reasoning = True

    def run_agent(messages, **kwargs):
        kwargs["on_token"]("reasoning", "kept from round one")
        kwargs["spin"]("thinking...")
        kwargs["on_token"]("reasoning", "broken fragment")
        kwargs["on_stream_reset"]()
        kwargs["on_token"]("reasoning", "the retry")
        return "ok"

    _chat(monkeypatch, run_agent)
    text = out.getvalue()
    assert "kept from round one" in text
    assert "the retry" in text
    assert "broken fragment" not in text


def _long_thought(messages, **kwargs):
    kwargs["on_token"]("reasoning", "\n".join(f"line {i}" for i in range(1, 9)))
    return "answer"


def test_a_long_thought_shows_its_first_lines_and_how_to_see_the_rest(monkeypatch, out, session):
    session.show_reasoning = True
    _chat(monkeypatch, _long_thought)
    text = out.getvalue()

    assert "line 5" in text and "line 6" not in text
    assert "3 more lines — /thoughts 1" in text


def test_thoughts_prints_the_last_turn_in_full(monkeypatch, out, session):
    session.show_reasoning = True
    _chat(monkeypatch, _long_thought)
    cli.cmd_thoughts("1")
    assert "line 8" in out.getvalue()

    cli.cmd_thoughts("9")
    assert "usage" in out.getvalue()


def test_reasoning_full_prints_every_line(monkeypatch, out, session):
    session.show_reasoning = True
    cli.cmd_reasoning("full")
    _chat(monkeypatch, _long_thought)
    text = out.getvalue()

    assert "line 8" in text and "more lines" not in text


def test_an_interrupted_turn_keeps_its_reasoning(monkeypatch, out, session):
    session.show_reasoning = True

    def run_agent(messages, **kwargs):
        kwargs["on_token"]("reasoning", "was thinking about this")
        raise cli.A.AgentInterrupted()

    _chat(monkeypatch, run_agent)
    text = out.getvalue()
    assert "was thinking about this" in text
    assert text.find("was thinking about this") < text.find("interrupted")
