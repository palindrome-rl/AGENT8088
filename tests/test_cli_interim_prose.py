"""What the model says before a tool call stays in the terminal scrollback.

Seen in use: the agent started writing a progress update, then the line vanished. The update only ever lived in the live region,
which is erased the moment the tool row replaces it, and only the final answer was printed for good. Each round's prose is now
printed once, above the tool rows it led to."""

import io
import threading
from contextlib import nullcontext

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
    _active = False

    def __init__(self):
        self.triggered = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def paused(self):
        return nullcontext()


def _chat(monkeypatch, run_agent):
    monkeypatch.setattr(cli, "EscListener", _Esc)
    monkeypatch.setattr(cli.A, "run_agent", run_agent)
    monkeypatch.setattr(cli, "_await_memory_capture", lambda stored: None)
    monkeypatch.setattr(cli, "_after_turn_plan_state", lambda: None)
    cli.do_chat("hello")


CALL = [{"name": "read_text", "arguments": {"filename": "notes.md"}}]


def _update_then_call(messages, **kwargs):
    kwargs["spin"]("thinking...")
    kwargs["on_token"]("content", "Reading the notes first, then I will write the summary.")
    kwargs["on_calls"](CALL)
    kwargs["spin"]("thinking...")
    kwargs["on_token"]("content", "Now writing the summary.")
    kwargs["on_calls"]([{"name": "write_file", "arguments": {"filename": "summary.md"}}])
    return "the final answer"


def test_each_rounds_update_is_kept_above_its_tool_rows_and_in_order(monkeypatch, out, session):
    _chat(monkeypatch, _update_then_call)
    text = out.getvalue()

    first, read, second, write, answer = (text.find(s) for s in (
        "Reading the notes first", "read_text", "Now writing the summary", "write_file", "the final answer"))
    assert -1 not in (first, read, second, write, answer)
    assert first < read < second < write < answer
    assert text.count("Reading the notes first") == 1        # printed once, not again with the answer


def test_a_round_with_no_prose_prints_nothing_extra(monkeypatch, out, session):
    def run_agent(messages, **kwargs):
        kwargs["spin"]("thinking...")
        kwargs["on_calls"](CALL)
        return "done"

    _chat(monkeypatch, run_agent)
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    assert any("read_text" in line for line in lines)
    assert not any(line.strip().startswith("Reading") for line in lines)


def test_the_update_is_kept_when_tool_detail_is_off(monkeypatch, out, session):
    session.verbose = "off"
    _chat(monkeypatch, _update_then_call)
    text = out.getvalue()
    assert "Reading the notes first" in text and "Now writing the summary" in text
    assert "read_text" not in text                            # /verbose off still hides the tool rows


def test_the_tool_call_itself_is_not_printed_as_prose(monkeypatch, out, session):
    def run_agent(messages, **kwargs):
        kwargs["spin"]("thinking...")
        kwargs["on_token"]("content", "Checking the file.\n")
        kwargs["on_token"]("content", '✿FUNCTION✿: read_text\n✿ARGS✿: {"filename": "notes.md"}')
        kwargs["on_calls"](CALL)
        return "done"

    _chat(monkeypatch, run_agent)
    text = out.getvalue()
    assert "Checking the file." in text
    assert "✿FUNCTION✿" not in text and "✿ARGS✿" not in text


def test_a_stream_reset_drops_a_broken_fragment_but_keeps_the_retry(monkeypatch, out, session):
    def run_agent(messages, **kwargs):
        kwargs["spin"]("thinking...")
        kwargs["on_token"]("content", "A broken fragmen")
        kwargs["on_stream_reset"]()
        kwargs["on_token"]("content", "The retried update.")
        kwargs["on_calls"](CALL)
        return "done"

    _chat(monkeypatch, run_agent)
    text = out.getvalue()
    assert "The retried update." in text
    assert "broken fragmen" not in text


def test_reasoning_and_the_update_both_stay_and_reasoning_comes_first(monkeypatch, out, session):
    session.show_reasoning = True

    def run_agent(messages, **kwargs):
        kwargs["spin"]("thinking...")
        kwargs["on_token"]("reasoning", "weighing two options")
        kwargs["on_token"]("content", "Going with the first option.")
        kwargs["on_calls"](CALL)
        return "done"

    _chat(monkeypatch, run_agent)
    text = out.getvalue()
    assert text.find("weighing two options") < text.find("Going with the first option.") < text.find("read_text")


@pytest.mark.parametrize('fallback', [False, True])
def test_permanent_updates_mask_known_secrets_and_system_fingerprints(monkeypatch, out, fallback):
    secret = 'synthetic-progress-secret-743928'
    rule = 'Synthetic private instruction for this regression only.'
    monkeypatch.setattr(cli.A, '_SECRET_VALUES', {secret})
    monkeypatch.setattr(cli.A, '_SYSTEM_FINGERPRINTS', {rule})
    if fallback:
        monkeypatch.setattr(cli, '_AnswerMarkdown', lambda *_a: (_ for _ in ()).throw(ValueError('render')))
    cli._print_round_prose('Checking the file. ' + secret + ' ' + rule)
    text = out.getvalue()
    assert 'Checking the file.' in text
    assert secret not in text and rule not in text
