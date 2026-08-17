"""Live-stream rendering: tool-call protocol never reaches the screen.

Agent8088's tool calls travel in the *content* channel as literal text, so the
CLI's live view used to echo `✿FUNCTION✿: write_file ✿ARGS✿: {...}` verbatim —
a whole source file as one line of escaped JSON. These tests pin the three
things that fixed it: the stream filter, the per-tool summary, and the height
cap that stops an oversized live region burning into the scrollback.

Everything here is pure string work on in-memory state: no files, no config,
no engine calls.
"""
import io

import pytest
from rich.console import Console

import agent8088.cli as cli

FUNC = "✿FUNCTION✿"
ARGS = "✿ARGS✿"


def feed_all(stream, text, chunk=1):
    """Push `text` through the filter in fixed-size deltas, as streaming would."""
    for i in range(0, len(text), chunk):
        stream.feed(text[i:i + chunk])
    return stream


# ---------------------------------------------------------------------------
# _StreamFilter — protocol suppression
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 7, 11, 64])
def test_sentinel_never_leaks_at_any_delta_boundary(chunk):
    """A sentinel routinely straddles two deltas ('✿FUNC' then 'TION✿'). Whatever
    the split, no fragment of it may be released as prose."""
    raw = (f"I'll write the file now.\n{FUNC}: write_file {ARGS}: "
           '{"filename": "library.py", "content": "import json\\nimport os\\n"}')
    stream = feed_all(cli._StreamFilter(), raw, chunk=chunk)

    prose = stream.prose_text()
    assert "✿" not in prose
    assert "FUNCTION" not in prose
    assert "library.py" not in prose
    assert prose.strip() == "I'll write the file now."


def test_prose_before_a_call_is_preserved_exactly():
    stream = feed_all(cli._StreamFilter(),
                      f"Step one.\nStep two.\n{FUNC}: read_text {ARGS}: "
                      '{"filename": "a.txt"}')
    assert stream.prose_text() == "Step one.\nStep two.\n"


def test_xml_tool_call_form_is_suppressed():
    stream = feed_all(cli._StreamFilter(),
                      'Here goes. <tool_call>{"name": "read_text", '
                      '"arguments": {"filename": "a.txt"}}</tool_call>')
    assert stream.prose_text() == "Here goes. "
    assert stream.tool["name"] == "read_text"


def test_bare_json_call_is_retracted_once_recognised():
    """The bare {"name": ..., "arguments": ...} form is only certain once the
    pattern completes, so the filter must be able to un-say what it released."""
    stream = cli._StreamFilter()
    feed_all(stream, 'Running it. {"name": "execute_shell", ')
    feed_all(stream, '"arguments": {"command": "ls"}}')
    assert stream.prose_text() == "Running it. "


def test_ordinary_braces_in_prose_are_not_withheld():
    """The brace guard is narrow on purpose: code and JSON in an answer must
    stream normally rather than stalling 64 characters behind."""
    prose = 'Use {"port": 8080} in the config, and {} for defaults.'
    stream = feed_all(cli._StreamFilter(), prose)
    assert stream.prose_text() == prose
    assert stream.tool is None


def test_partial_json_opener_is_withheld_until_resolved():
    stream = feed_all(cli._StreamFilter(), 'Done. {"nam')
    assert stream.prose_text() == "Done. "


def test_reset_lets_prose_stream_again_after_a_tool_round():
    stream = feed_all(cli._StreamFilter(), f"{FUNC}: read_text {ARGS}: " '{"filename": "a.txt"}')
    assert stream.prose_text() == ""
    stream.reset()
    feed_all(stream, "The file lists three books.")
    assert stream.prose_text() == "The file lists three books."
    assert stream.tool is None


def test_the_screenshot_blob_produces_no_visible_protocol():
    """Regression for the reported output: present_plan followed by a large
    write_file rendered as a wall of escaped JSON."""
    body = "\\n".join(f"line {i}" for i in range(200))
    raw = (f"{FUNC}: present_plan {ARGS}: " '{"plan": "## Goal\\nBuild library.py"}'
           f"\n{FUNC}: write_file {ARGS}: "
           '{"filename": "library.py", "content": "' + body + '"}')
    stream = feed_all(cli._StreamFilter(), raw, chunk=13)
    assert stream.prose_text() == ""
    assert "import json" not in stream.prose_text()


# ---------------------------------------------------------------------------
# _StreamFilter — status labels
# ---------------------------------------------------------------------------
def test_status_label_names_the_file_and_counts_lines():
    stream = feed_all(cli._StreamFilter(),
                      f"{FUNC}: write_file {ARGS}: "
                      '{"filename": "library.py", "content": "a\\nb\\nc')
    assert stream.status_label() == "writing library.py · 3 lines"


def test_status_label_for_a_plan_omits_the_oversized_subject():
    """A plan's first argument is the whole plan; naming it would be the blob
    again, so only the verb and the line counter are shown."""
    stream = feed_all(cli._StreamFilter(),
                      f"{FUNC}: present_plan {ARGS}: " '{"plan": "' + "x" * 300 + '"}')
    assert stream.status_label() == "composing plan"


def test_status_label_before_the_name_is_parseable():
    stream = feed_all(cli._StreamFilter(), f"{FUNC}")
    assert stream.status_label() == "calling a tool"


def test_status_label_resolves_an_alias():
    stream = feed_all(cli._StreamFilter(), f"{FUNC}: bash {ARGS}: " '{"command": "ls"}')
    assert stream.status_label() == "preparing command ls"


def test_status_label_is_thinking_when_no_call_is_streaming():
    assert cli._StreamFilter().status_label() == "thinking"


# ---------------------------------------------------------------------------
# _tool_summary — semantic subjects instead of argument dumps
# ---------------------------------------------------------------------------
def test_write_file_summary_reports_size_not_content():
    summary = cli._tool_summary("write_file",
                                {"filename": "library.py", "content": "a\nb\nc"})
    assert summary == "library.py (3 lines, 5 B)"


def test_write_file_summary_stays_short_for_a_large_file():
    summary = cli._tool_summary("write_file",
                                {"filename": "library.py", "content": "x" * 4000})
    assert len(summary) < 60
    assert "xxxx" not in summary


def test_read_text_summary_is_the_path():
    assert cli._tool_summary("read_text", {"filename": "src/app.py"}) == "src/app.py"


def test_shell_summary_is_the_command():
    assert cli._tool_summary("execute_shell", {"command": "python library.py"}) == "python library.py"


def test_plan_summary_skips_the_heading_for_the_goal():
    plan = "## Goal\nBuild library.py — a CLI library manager\n\n## Steps\n1. ..."
    assert cli._tool_summary("present_plan", {"plan": plan}) == "Build library.py — a CLI library manager"


def test_subagent_summary_pairs_the_selector_with_the_task():
    summary = cli._tool_summary("spawn_subagent",
                                {"agent_type": "explore", "task": "find every TODO in the repo"})
    assert summary == "explore · find every TODO in the repo"


def test_unknown_tool_falls_back_to_clipped_arguments():
    summary = cli._tool_summary("mystery_tool", {"blob": "y" * 500})
    assert len(summary) < 120
    assert summary.endswith("…")


def test_summary_of_a_tool_with_no_arguments_is_empty():
    assert cli._tool_summary("describe_capabilities", {}) == ""


def test_format_args_clips_and_flattens_newlines():
    rendered = cli._format_args({"content": "a\nb"}, limit=100)
    assert rendered == 'content="a\\nb"'


# ---------------------------------------------------------------------------
# Height cap — the live region must never outgrow the viewport
# ---------------------------------------------------------------------------
def test_window_tail_counts_wrapped_rows_not_newlines():
    """One long line wraps to many rows; budgeting by newline alone is what let
    the live panel outgrow the terminal in the first place."""
    body, truncated = cli._window_tail("x" * 400, max_rows=3, width=40)
    assert truncated
    assert len(body) <= 3 * 40


def test_window_tail_keeps_the_most_recent_lines():
    text = "\n".join(f"line {i}" for i in range(50))
    body, truncated = cli._window_tail(text, max_rows=4, width=80)
    assert truncated
    assert body.splitlines()[-1] == "line 49"
    assert len(body.splitlines()) == 4


def test_window_tail_passes_short_text_through_untouched():
    body, truncated = cli._window_tail("one\ntwo", max_rows=10, width=80)
    assert (body, truncated) == ("one\ntwo", False)


def test_stream_view_never_exceeds_the_terminal_height(monkeypatch):
    output = io.StringIO()
    console = Console(file=output, width=80, height=24, color_system=None,
                      legacy_windows=False)
    monkeypatch.setattr(cli, "console", console)

    console.print(cli._stream_view([], "\n".join(f"line {i}" for i in range(500))))

    assert len(output.getvalue().splitlines()) <= 24


def test_stream_view_shares_the_budget_with_a_visible_reasoning_pane(monkeypatch):
    """`/reasoning on` adds a second pane; together they must still fit."""
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=80, height=24, color_system=None,
                                legacy_windows=False))

    cli.console.print(cli._stream_view(
        ["\n".join(f"thought {i}" for i in range(200))],
        "\n".join(f"line {i}" for i in range(200))))

    rendered = output.getvalue()
    assert len(rendered.splitlines()) <= 24
    assert "thought 199" in rendered
    assert "line 199" in rendered


def test_stream_view_drops_the_blank_gap_before_a_tool_call(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=80, height=24, color_system=None,
                                legacy_windows=False))

    cli.console.print(cli._stream_view([], "I'll write the file.\n\n\n"))

    # Panel border, one line of prose, panel border — no dead rows between.
    assert len(output.getvalue().rstrip().splitlines()) == 3


def test_stream_view_says_where_the_earlier_lines_went(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=80, height=24, color_system=None,
                                legacy_windows=False))

    cli.console.print(cli._stream_view([], "\n".join(f"line {i}" for i in range(500))))

    rendered = output.getvalue()
    assert "the full answer prints below" in rendered
    assert "line 499" in rendered


# ---------------------------------------------------------------------------
# Persistent footer — the bar must survive the whole turn, and cost one row
# ---------------------------------------------------------------------------
def test_footer_state_word_distinguishes_working_from_ready():
    """The prompt and the turn share one bar definition; only the state differs.

    A second copy of the layout is how the two halves drifted apart before."""
    ready = "".join(text for _, text in cli._status_bar_fragments())
    working = "".join(text for _, text in cli._status_bar_fragments("working"))

    assert ready.endswith("● ready ")
    assert working.endswith("● working ")
    # Everything up to the state word is byte-identical between the two.
    assert ready[:ready.rindex("●")] == working[:working.rindex("●")]


def test_footer_line_renders_every_fragment_style():
    """An unmapped prompt_toolkit style would silently render as default text."""
    styles = {style for style, _ in cli._status_bar_fragments("working")}
    assert styles <= set(cli._FOOTER_STYLES), (
        f"unmapped footer styles: {styles - set(cli._FOOTER_STYLES)}")


def test_footer_line_stays_one_row_when_the_model_name_is_long(monkeypatch):
    """The bar runs past 100 columns with a long provider:model. Wrapped, it
    would quietly eat a second row out of the live region on every frame."""
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=60, height=24, color_system=None,
                                legacy_windows=False))
    monkeypatch.setattr(cli.A, "MODEL_NAME", "a-very-long-model-name-that-overflows")

    cli.console.print(cli._footer_line("working"))

    assert len(output.getvalue().rstrip("\n").splitlines()) == 1


def test_stream_budget_reserves_a_row_for_the_footer(monkeypatch):
    """_stream_view fills the budget exactly, so the footer needs its own row
    carved out of it or the live region grows one line past the viewport."""
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=80, height=24, color_system=None,
                                legacy_windows=False))

    body = cli._stream_view([], "\n".join(f"line {i}" for i in range(500)))
    cli.console.print(cli.Group(body, cli._footer_line("working")))

    assert len(output.getvalue().rstrip("\n").splitlines()) <= 24


def test_footer_live_appends_the_footer_to_whatever_it_is_given(monkeypatch):
    """The footer is part of the frame, not a separately addressed row — that is
    what stops it tearing away from the content above it."""
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=100, height=24, color_system=None,
                                legacy_windows=False))

    class _FakeLive:
        is_started = True
        def __init__(self): self.renderable = None
        def update(self, renderable, refresh=False): self.renderable = renderable
        def refresh(self): cli.console.print(self.renderable)

    fake = _FakeLive()
    footer_live = cli._FooterLive(fake)
    footer_live.update(cli.Text("streaming answer"))
    footer_live._paint()

    rendered = output.getvalue()
    assert "streaming answer" in rendered
    assert "● working" in rendered


def test_footer_live_repaints_only_when_something_changed():
    """Rich's own refresh thread repaints unconditionally; that churn is the
    flicker. Only new content or a spinner owed a tick may trigger a frame."""
    footer_live = cli._FooterLive(object())

    footer_live.update(cli.Text("prose"))
    assert footer_live._dirty is True

    footer_live._body, footer_live._dirty = cli.Text("prose"), False
    assert cli._FooterLive._animates(footer_live._body) is False, (
        "a static prose panel must not schedule a repaint on its own")

    spinner = cli._StatusLine("thinking", 0.0, [0], interruptible=True)
    assert cli._FooterLive._animates(spinner) is True, (
        "the spinner still has to animate while the model is quiet")


def test_footer_keeps_the_state_word_when_the_bar_does_not_fit(monkeypatch):
    """Rich's own overflow trims the right-hand end, which drops '● working' —
    the one fragment that has to survive, since it is how the bar shows the turn
    is still running. The middle detail gives way instead."""
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=44, height=24, color_system=None,
                                legacy_windows=False))
    monkeypatch.setattr(cli.A, "MODEL_NAME", "a-very-long-model-name-that-overflows")

    cli.console.print(cli._footer_line("working"))

    rendered = output.getvalue().rstrip("\n")
    assert len(rendered.splitlines()) == 1
    assert rendered.startswith(" ◆ 8088 ")
    assert rendered.endswith("● working ")


def test_footer_drops_a_details_separator_with_it(monkeypatch):
    """Dropping a detail must take its '│' with it, or the bar shows a stray
    separator fencing off nothing: 'readonly │  │ ● working'."""
    output = io.StringIO()
    monkeypatch.setattr(cli, "console",
                        Console(file=output, width=70, height=24, color_system=None,
                                legacy_windows=False))

    cli.console.print(cli._footer_line("working"))

    rendered = output.getvalue().rstrip("\n")
    assert len(rendered.splitlines()) == 1
    assert "│  │" not in rendered
    assert rendered.endswith("● working ")
