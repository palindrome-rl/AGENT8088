"""Memory has to survive the exit, and the model has to know it exists.

Both measured live on ollama-cloud:glm-5.3 in one run: the user said "Remember
this: I always deploy to Cloudflare Workers, never to Vercel." and typed /exit.

  - the model answered "in this environment I don't have a tool to save
    persistent long-term memories", having searched for one and found only
    memory_forget -- nothing in its prompt said the store existed
  - memory.db came out of the run with `memories -> 0 rows`: capture runs on a
    daemon thread, and /exit took the interpreter down before it finished
"""

import io
import threading
import time


# -- the capture thread outlives the prompt, not the process -----------------

def test_a_quick_exit_waits_for_the_capture_thread(monkeypatch):
    """`/exit` right after stating a preference used to discard it.

    capture() runs on a daemon thread so the prompt comes back immediately;
    daemon threads are killed at interpreter shutdown, so the extraction call
    in flight was simply lost.
    """
    from agent8088 import cli

    finished = []

    def slow_capture():
        time.sleep(0.3)
        finished.append("stored")

    thread = threading.Thread(target=slow_capture, daemon=True)
    thread.start()
    monkeypatch.setattr(cli.A, "memory_capture_thread", thread, raising=False)

    cli._flush_memory_capture(timeout=5.0)

    assert finished == ["stored"], "the turn's facts were dropped on exit"
    assert not thread.is_alive()


def test_the_flush_gives_up_rather_than_hanging_the_exit(monkeypatch):
    """A wedged extraction call must not make /exit unkillable."""
    from agent8088 import cli

    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    monkeypatch.setattr(cli.A, "memory_capture_thread", thread, raising=False)

    started = time.monotonic()
    try:
        cli._flush_memory_capture(timeout=0.2)
        assert time.monotonic() - started < 3.0
    finally:
        release.set()


def test_the_flush_is_a_no_op_when_nothing_is_running(monkeypatch):
    from agent8088 import cli

    monkeypatch.setattr(cli.A, "memory_capture_thread", None, raising=False)

    cli._flush_memory_capture(timeout=5.0)  # must not raise


# -- the model is told the store exists --------------------------------------

def test_the_model_is_told_the_memory_store_exists(engine, monkeypatch):
    """With memory on and nothing recalled, the prompt said nothing at all.

    recall_block() returns "" on an empty store, so a first-time user got a
    model that correctly reported having no memory tool -- and then offered to
    fake one with a file.
    """
    monkeypatch.setattr(engine.memory, "enabled", lambda: True)
    monkeypatch.setattr(engine.memory, "recall_block",
                        lambda *a, **k: "", raising=False)

    prompt = engine._recalled_memory_prompt(
        [{"role": "user", "content": "remember I deploy to Cloudflare"}], "BASE")
    text = prompt() if callable(prompt) else prompt

    assert "BASE" in text
    assert engine.MEMORY_CAPABILITY_NOTE in text


def test_recalled_facts_still_reach_the_prompt(engine, monkeypatch):
    """The note must be added alongside recall, not instead of it."""
    monkeypatch.setattr(engine.memory, "enabled", lambda: True)
    monkeypatch.setattr(engine.memory, "recall_block",
                        lambda *a, **k: "Recalled context\n- deploys to Cloudflare\n",
                        raising=False)

    prompt = engine._recalled_memory_prompt(
        [{"role": "user", "content": "where do I deploy?"}], "BASE")
    text = prompt() if callable(prompt) else prompt

    assert "deploys to Cloudflare" in text
    assert engine.MEMORY_CAPABILITY_NOTE in text


def test_memory_off_adds_nothing(engine, monkeypatch):
    """Nobody pays for a capability the install does not have."""
    monkeypatch.setattr(engine.memory, "enabled", lambda: False)

    prompt = engine._recalled_memory_prompt(
        [{"role": "user", "content": "hello"}], "BASE")

    assert prompt == "BASE"


# -- Windows: a redirected stdout must not kill the run ----------------------

def test_an_arrow_survives_a_redirected_stdout():
    """`agent8088 > run.log` died on the ↑ in the token counter.

    Piped stdout on Windows takes the locale encoding (cp1252 here), which has
    no U+2191, so the first status line raised UnicodeEncodeError and took the
    session with it.
    """
    from agent8088 import cli

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
    cli._force_utf8_streams(stream)

    stream.write("last 12.3s ↑228 tokens")  # must not raise
    stream.flush()


def test_a_stream_that_cannot_be_reconfigured_is_left_alone():
    """Not every stdout is a TextIOWrapper; the helper must never be fatal."""
    from agent8088 import cli

    cli._force_utf8_streams(io.StringIO(), None, object())


# -- the "saving" indicator is transient -------------------------------------

def _terminal_console(monkeypatch):
    from rich.console import Console
    from agent8088 import cli

    monkeypatch.setenv("TERM", "xterm-256color")
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, force_terminal=True, force_interactive=True, width=100))
    return out


def _saving_line_was_erased(text):
    """The saving text must be followed by an erase-line before anything else."""
    tail = text[text.rindex("saving"):]
    return "\x1b[2K" in tail


def test_the_turn_end_saving_line_is_erased_once_the_save_finishes(monkeypatch):
    """`⏺ memory · saving…` stayed in the scrollback above `stored 1 new memory`.

    It was a plain console.print before the join, so nothing could clear it.
    """
    from agent8088 import cli

    out = _terminal_console(monkeypatch)
    stored = []

    def capture():
        time.sleep(0.3)
        stored.append({"text": "user prefers tabs"})

    thread = threading.Thread(target=capture, daemon=True)
    thread.start()
    monkeypatch.setattr(cli.S, "memory_notifications", "on")
    monkeypatch.setattr(cli.A, "memory_capture_thread", thread, raising=False)
    monkeypatch.setattr(cli, "_pending_captures", [])

    cli._await_memory_capture(stored)

    text = out.getvalue()
    assert "saving" in text
    assert _saving_line_was_erased(text), repr(text)
    assert "new memory" in text[text.rindex("saving"):]


def test_the_exit_saving_line_is_erased_once_the_save_finishes(monkeypatch):
    from agent8088 import cli

    out = _terminal_console(monkeypatch)
    thread = threading.Thread(target=time.sleep, args=(0.3,), daemon=True)
    thread.start()
    monkeypatch.setattr(cli.A, "memory_capture_thread", thread, raising=False)
    monkeypatch.setattr(cli, "_pending_captures", [])

    cli._flush_memory_capture(timeout=5.0)

    text = out.getvalue()
    assert "saving" in text
    assert _saving_line_was_erased(text), repr(text)
